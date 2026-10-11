"""Governed proposals inside the app: queue, batch, decide, then execute through Workspace.

  submit()     validate against governor.api, check the agent, record its forecast, queue a request
  run_batch()  per account: refresh the balance, fetch order books, build the portfolio from venue
               positions, check the loss breaker, decide, record the batch, then send approved stakes
               through Workspace.proposal, so mandates, account ceilings and user approval still apply
  lookup()     a request's status, the governor's decision and the downstream order
  score_forecasts()  record outcomes of settled markets, so agent reliability (and sizing) updates

Request status: queued -> rejected (see decision.reasonCodes or note) | approved (execution pending)
-> submitted (see the order) | skipped | executionFailed. Every status in a batch is recorded before any
order is sent, and the next batch resumes approved requests, so a crash mid-batch cannot wedge the queue.

State: s["governorRequests"], newest first (JSON mode keeps older ones in slim form; Postgres keeps them
in governor_requests, reached through find_request). The governor package stays pure; this module is where
it meets the venue, the clock and the app state.
"""
import copy
import datetime as dt
import time
from types import SimpleNamespace
import uuid

from adapters import VenueError
from engine import event
from governor import api, records
from governor.allocator import decide
from governor.breaker import check, reset
from governor.fees import taker_fee
from governor.models import Exposure, Portfolio, Proposal
from governor.money import CENT, ZERO, round_up, to_decimal
import trading_rules
from governor.snapshot import build_snapshot, order_book

SCHEMA_PATH = "/api/governor/schema"
MAX_EVALUATION_ATTEMPTS = 3  # passes a request may fail to be evaluated before it is rejected
VENUE_RETRY_SECONDS = 0.5  # pause before the one retry of an order book read the venue did not answer


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def _equity(balance):
    """Cash + reserved + marked positions: the base for caps and the loss breaker (as Workspace.apply_balance)."""
    return (to_decimal(balance["cash"], "cash") + to_decimal(balance.get("reserved", 0), "reserved")
            + to_decimal(balance["positionValue"], "positionValue"))


def _invalid(errors):
    return 400, {"error": "Invalid proposal.", "errors": errors, "schema": SCHEMA_PATH}


class GovernorService:
    def __init__(self, workspace, policy=None, clock=utc_now, new_id=lambda: str(uuid.uuid4()),
                 find_request=None):
        """find_request(agent_id, proposal_id) returns an older stored request (Postgres) or None.

        policy: a fixed Policy for replays and tests. Otherwise each workspace's own trading rules (trading_rules.py)
        are the policy, read on every call, and a workspace without rules cannot trade.
        """
        self.workspace, self.fixed_policy, self.clock, self.new_id = workspace, policy, clock, new_id
        self.find_request = find_request

    def policy_for(self, s):
        """The policy for this workspace: its user-set trading rules, or None if they are not set yet."""
        return self.fixed_policy or trading_rules.to_policy(s)

    def schema(self, s=None):
        return api.describe(self.policy_for(s) if s is not None else self.fixed_policy)

    # Submission and lookup.

    def submit(self, s, payload):
        """Validate and queue one proposal. Returns (http_status, body); 202 queued, 200 identical retry."""
        now = self.clock()
        policy = self.policy_for(s)
        if policy is None:
            return _invalid([{"field": "", "error": "this workspace has no trading rules yet; its owner must set them "
                                                    "before agents can propose trades"}])
        normalized, errors = api.parse_submission(payload, received_at=now, policy=policy)
        if errors:
            return _invalid(errors)
        agent = next((a for a in s["agentConfigs"] if a["id"] == normalized["agentId"]), None)
        if agent is None:
            return _invalid([{"field": "agentId", "error": "is not a configured agent"}])
        requests = s.setdefault("governorRequests", [])
        fingerprint = api.payload_hash(payload)
        # A retry must reach its original record even if the agent was paused since, or the request has
        # left the loaded window, so look it up before any status check.
        prior = next((r for r in requests if r["agentId"] == agent["id"] and r["proposalId"] == normalized["proposalId"]), None)
        if prior is None and self.find_request:
            prior = self.find_request(agent["id"], normalized["proposalId"])
        if prior:
            if prior["payloadHash"] != fingerprint:
                return 409, {"error": "This proposalId was already used with a different payload.",
                             "governorProposalId": prior["governorProposalId"]}
            return 200, self._view(s, prior)
        if agent.get("status") != "active" or not agent.get("mandate"):
            return _invalid([{"field": "agentId", "error": "must be an active agent with a saved mandate"}])
        gid = self.new_id()
        proposal = Proposal(gid, agent["id"], normalized["marketId"], normalized["side"],
                            normalized["estimatedYesProbability"], normalized["maxBudgetUsd"], now,
                            dt.datetime.fromisoformat(normalized["expiresAt"]), normalized["maxEntryPrice"])
        records.record_forecast(s, proposal)
        requests.insert(0, {"governorProposalId": gid, "proposalId": normalized["proposalId"], "agentId": agent["id"],
                            "accountId": agent["accountId"], "payloadHash": fingerprint, "receivedAt": now.isoformat(),
                            "status": "queued", "proposal": records.proposal_to_dict(proposal),
                            "reasoning": normalized["reasoning"]})
        requests.sort(reverse=True, key=lambda r: (dt.datetime.fromisoformat(r["receivedAt"]), r["governorProposalId"]))
        return 202, {"governorProposalId": gid, "proposalId": normalized["proposalId"], "status": "queued"}

    def lookup(self, s, governor_proposal_id):
        request = next((r for r in s.get("governorRequests", []) if r["governorProposalId"] == governor_proposal_id), None)
        return None if request is None else self._view(s, request)

    def views(self, s, requests, orders=None):
        """_view for many requests, indexing decisions and orders once instead of scanning them per request."""
        decisions = {}
        if orders is None:
            orders = {o["id"]: o for o in s["orders"]}
        for d in s.get("governorDecisions", []):
            decisions.setdefault(d["proposalId"], d)  # first match wins, as in _view
        return [self._view(s, r, decisions, orders) for r in requests]

    def _view(self, s, request, decisions=None, orders=None):
        if decisions is None:
            decision = next((d for d in s.get("governorDecisions", []) if d["proposalId"] == request["governorProposalId"]), None)
        else:
            decision = decisions.get(request["governorProposalId"])
        if not request.get("orderId"):
            order = None
        elif orders is None:
            order = next((o for o in s["orders"] if o["id"] == request["orderId"]), None)
        else:
            order = orders.get(request["orderId"])
        return {
            "governorProposalId": request["governorProposalId"], "proposalId": request["proposalId"],
            "agentId": request["agentId"], "marketId": request["proposal"]["marketId"],
            "side": request["proposal"]["side"], "status": request["status"], "receivedAt": request["receivedAt"],
            "note": request.get("note"),
            "decision": None if decision is None else {
                k: decision[k] for k in ("decision", "approvedBudgetUsd", "contracts", "limitPrice", "reasonCodes",
                                         "reliabilityScore", "policyVersion", "batchId")},
            "order": None if order is None else {
                "id": order["id"], "status": order["status"], "decision": order.get("decision"),
                "amount": order.get("amount"), "quantity": order.get("quantity"), "actualCost": order.get("actualCost")},
        }

    # Batches. A pass is plan() under the workspace lock, fetch() with the lock released (venue reads only),
    # then run_batch(s, fetched) under the lock again. run_batch(s) alone plans and fetches inline.

    def plan(self, s):
        """The venue reads the next pass needs: per connected account, its record, queued requests and markets."""
        queued = [r for r in s.get("governorRequests", []) if r["status"] == "queued"]
        plans = {}
        for account in s["accounts"]:
            items = [r for r in queued if r["accountId"] == account["id"]]
            if items and account.get("status") != "disconnected":
                plans[account["id"]] = {"account": copy.deepcopy(account),
                                        "requestIds": sorted(r["governorProposalId"] for r in items),
                                        "markets": sorted({r["proposal"]["marketId"] for r in items})}
        return plans

    def fetch(self, plans):
        """Balances and order books for a plan. Reads the venue only and changes no state, so it can run unlocked."""
        fetched = {}
        for account_id, item in plans.items():
            try:
                venue = self.workspace.venue(item["account"])
            except (ValueError, KeyError):
                fetched[account_id] = {**item, "error": "The venue account's credentials are unavailable."}
                continue
            started = self.clock()
            balance, balance_failure = self.workspace.read_balance(item["account"])
            books, unavailable = [], {}
            for market_id in item["markets"]:
                raw, problem = self._read_book(venue, market_id)
                if raw is None:  # left out of the snapshot: retried next pass if transient, else marketNotInSnapshot
                    unavailable[market_id] = problem
                    print(f"Governor: order book for {market_id} unavailable "
                          f"({'transient' if problem['transient'] else 'refused'}): {problem['reason']}", flush=True)
                    continue
                books.append(raw)
            fetched[account_id] = {**item, "startedAt": started, "balance": balance, "balanceFailure": balance_failure,
                                   "books": books, "unavailable": unavailable}
        return fetched

    def _read_book(self, venue, market_id):
        """(raw book, None), or (None, {"reason", "transient"}). A read the venue did not answer is retried once."""
        for attempt in range(2):
            try:
                raw = venue.get_orderbook(market_id)
                order_book(raw)  # validate now, so an unusable book is reported rather than failing the batch
                return raw, None
            except VenueError as exc:
                problem = {"reason": str(exc) or "The venue did not return the order book.", "transient": exc.transient}
            except ValueError as exc:
                return None, {"reason": f"The venue returned an unusable order book ({exc}).", "transient": False}
            if not problem["transient"]:
                return None, problem
            if attempt == 0:
                time.sleep(VENUE_RETRY_SECONDS)
        return None, problem

    def run_batch(self, s, fetched=None):
        """Decide the planned queued requests, one batch per account. Returns a summary per account.

        One account's failure never stops the others: its still-queued requests count a failed attempt and
        are rejected after MAX_EVALUATION_ATTEMPTS, so a persistent fault cannot retry forever.
        """
        s["clock"] = self.clock().isoformat()
        self._resume(s)
        connected = {a["id"] for a in s["accounts"] if a.get("status") != "disconnected"}
        self._reject_all([r for r in s.get("governorRequests", []) if r["status"] == "queued" and r["accountId"] not in connected],
                         "The agent's venue account is not connected.")
        if fetched is None:
            fetched = self.fetch(self.plan(s))
        results = []
        for account_id in sorted(fetched):
            planned = set(fetched[account_id]["requestIds"])
            items = sorted((r for r in s.get("governorRequests", []) if r["status"] == "queued" and r["governorProposalId"] in planned),
                           key=lambda r: r["governorProposalId"])
            if not items:
                continue
            try:
                results.append(self._run_account(s, account_id, items, fetched[account_id]))
            except Exception as exc:  # contain the failure to this account's requests
                for r in items:
                    if r["status"] == "queued":
                        r["attempts"] = r.get("attempts", 0) + 1
                        if r["attempts"] >= MAX_EVALUATION_ATTEMPTS:
                            r.update(status="rejected", note=f"Could not be evaluated after {r['attempts']} attempts ({type(exc).__name__}).")
                results.append({"accountId": account_id, "decided": 0, "error": type(exc).__name__})
        return results

    def _defer(self, items, why):
        """Leave requests queued for the next pass, counting an attempt; reject after MAX_EVALUATION_ATTEMPTS."""
        for r in items:
            r["attempts"] = r.get("attempts", 0) + 1
            if r["attempts"] >= MAX_EVALUATION_ATTEMPTS:
                r.update(status="rejected", note=f"{why} after {r['attempts']} attempts.")
            else:
                r["note"] = f"{why}; trying again in the next batch."

    def _reject_all(self, items, note):
        for r in items:
            r.update(status="rejected", note=note)

    def _run_account(self, s, account_id, items, fetched):
        account = next(a for a in s["accounts"] if a["id"] == account_id)
        live = {a["id"] for a in s["agentConfigs"] if a.get("status") == "active" and a.get("mandate")}
        self._reject_all([r for r in items if r["agentId"] not in live],
                         "The agent was paused or deleted before its proposal was decided.")
        items = [r for r in items if r["agentId"] in live]
        if not items:
            return {"accountId": account_id, "decided": 0, "note": "no live agents"}
        policy = self.policy_for(s)
        if policy is None:
            self._reject_all(items, "Trading rules are not set for this workspace.")
            return {"accountId": account_id, "decided": 0, "note": "no trading rules"}
        if fetched.get("error"):
            self._reject_all(items, fetched["error"])
            return {"accountId": account_id, "decided": 0, "note": fetched["error"]}
        venue = self.workspace.venue(account)
        started = fetched["startedAt"]
        balance, refreshed = self._newest_balance(s, account, fetched["balance"], started,
                                                  fetched.get("balanceFailure") or "refused")
        # A venue that did not answer is asked again next pass rather than turned into a rejection.
        if balance is None and fetched.get("balanceFailure") == "transient":
            self._defer(items, "Kalshi did not answer the balance check")
            return {"accountId": account_id, "decided": 0, "note": "balance read deferred"}
        unavailable = fetched.get("unavailable") or {}
        waiting = [r for r in items if (unavailable.get(r["proposal"]["marketId"]) or {}).get("transient")]
        if waiting:
            self._defer(waiting, "Kalshi did not answer the order book read")
            items = [r for r in items if r not in waiting]
            if not items:
                return {"accountId": account_id, "decided": 0, "note": "order book reads deferred"}
        in_flight = self._in_flight(s, account, started, balance if refreshed else None, fetched["balance"])
        books = fetched["books"]
        snapshot_id = self.new_id()
        snapshot = build_snapshot(books, snapshot_id=snapshot_id, taken_at=started)
        now = self.clock()
        portfolio = self._portfolio(s, account, balance, snapshot, now, policy, in_flight)
        proposals = [records.proposal_from_dict(r["proposal"]) for r in items]
        reliability = records.reliability(s, policy)
        decisions = decide(proposals, snapshot, portfolio, reliability, now, policy)
        batch = records.batch_record(batch_id=self.new_id(), account_id=account_id, started_at=started, evaluated_at=now,
                                     snapshot_id=snapshot_id, books=books, proposals=proposals, portfolio=portfolio,
                                     reliability=reliability, policy=policy)
        records.record_batch(s, batch, decisions, now)
        by_id = {r["governorProposalId"]: r for r in items}
        # Record every outcome before sending any order: execution saves state (a crash-safe reservation),
        # and a request must never be saved as queued once its decision is recorded.
        for d in decisions:
            problem = unavailable.get(d.market_id)
            by_id[d.proposal_id].update(status="rejected" if d.outcome == "rejected" else "approved",
                                        batchId=batch["id"], decision=d.outcome,
                                        note=f"Market data unavailable: {problem['reason']}" if problem else None)
        for d in decisions:
            if d.outcome != "rejected":
                self._execute_safely(s, by_id[d.proposal_id], d, venue, snapshot.books[d.market_id], policy)
        return {"accountId": account_id, "batchId": batch["id"], "decided": len(decisions)}

    def _newest_balance(self, s, account, fetched, started, failure="refused"):
        """The balance to decide on, and whether it is a refresh newer than the governor's own read.

        Normally the fetched read, which started at `started` with the lock released. If the account was refreshed
        under the lock after that (e.g. Workspace.evaluate's refresh after a fill), that newer balance is used
        instead, including when the unlocked read failed: applying the older read, or marking the account errored,
        would roll back a fresh balance. `failure` says why the fetched read failed (Workspace.read_balance).
        """
        current = account.get("balance")
        if current and not account.get("stale"):
            try:
                newer = dt.datetime.fromisoformat(current["asOf"]) > (
                    dt.datetime.fromisoformat(fetched["asOf"]) if fetched is not None else started)
            except (KeyError, TypeError, ValueError):
                newer = False  # an unreadable timestamp: fall back to the fetched read
            if newer:
                return current, True
        return self.workspace.apply_balance(s, account, fetched, failure), False

    def _in_flight(self, s, account, started, refreshed, fetched):
        """Orders the venue answered while the lock was released whose fill the chosen positions may not show.

        Counted as exposure on top of the positions. Only orders answered at or after `started` (the unlocked read's
        start) are candidates; earlier fills are in either read. Then:
        - deciding on the fetched read (refreshed is None): every candidate counts, since a fill that landed while
          the read ran may or may not be in it;
        - deciding on a newer refresh: a ticker's filled candidates are skipped only if the refreshed position grew
          by at least their quantity since the fetched read. Otherwise (Kalshi's positions lagging behind the fill,
          or no fetched read to compare with) they count. Committed orders without a fill count always count.
        Errs toward smaller stakes. Committed `submitted` orders answered before `started` are not counted here
        (nor in the positions, if the venue never reported their fill): a known gap, see the PR #16 follow-ups.

        Every order is scanned: orders are stored by creation time, but one that waited for the user's approval is
        answered when approved, which can be long after it was created, so there is no safe early cutoff.
        """
        def answered_in_window(o):
            try:
                return dt.datetime.fromisoformat(o["executedAt"]) >= started
            except (KeyError, TypeError, ValueError):
                return False  # no executedAt: placed before it was recorded, so long before this read
        candidates = [o for o in s["orders"] if o.get("accountId") == account["id"]
                      and (o.get("filled") or (o.get("committed") and o.get("status") in ("submitted", "open")))
                      and answered_in_window(o)]
        if refreshed is None or not candidates:
            return candidates

        def positions(balance):
            return {p.get("ticker"): to_decimal(p.get("position_fp", 0), "position_fp")
                    for p in (balance or {}).get("positions") or []}
        now_held, before = positions(refreshed), positions(fetched) if fetched is not None else None
        counted = []
        for ticker in {o["market"]["id"] for o in candidates}:
            orders = [o for o in candidates if o["market"]["id"] == ticker]
            filled = [o for o in orders if o.get("filled") and o.get("quantity")]
            counted += [o for o in orders if o not in filled]  # quantity unknown: cannot be checked against positions
            expected = sum((to_decimal(o["quantity"], "quantity") * (1 if o["direction"] == "Yes" else -1) for o in filled), ZERO)
            grown = None if before is None else now_held.get(ticker, ZERO) - before.get(ticker, ZERO)
            shown = grown is not None and expected != ZERO and (grown >= expected if expected > ZERO else grown <= expected)
            if not shown:
                counted += filled
        return [o for o in candidates if o in counted]

    def _portfolio(self, s, account, balance, snapshot, now, policy, in_flight=()):
        state = records.breaker_state(s, account["id"])
        paused = s["permissions"].get("automation") is False
        if balance is None:
            return Portfolio(ZERO, ZERO, (), paused=paused, breaker_latched=state.latched, valuation_stale=True)
        equity = _equity(balance)
        baseline = account.get("baseline") or {}
        day_open = baseline.get("equity") if baseline.get("day") == now.date().isoformat() else None
        result = check(state, equity=equity, day_open_equity=day_open, now=now, policy=policy)
        if result.tripped:
            records.save_breaker(s, account["id"], result.state)
            records.record_breaker_event(s, account["id"], {
                "action": "breakerLatched", "at": now.isoformat(), "reason": result.state.reason, "equity": str(equity),
                "base": None if result.base is None else str(result.base)}, self.new_id())
            event(s, f"Loss breaker latched: {result.state.reason}", "BREAKER")
        return Portfolio(equity, max(ZERO, to_decimal(balance["cash"], "cash")),
                         self._exposures(s, account, balance, snapshot, now, in_flight), paused=paused,
                         breaker_latched=result.state.latched, valuation_stale=bool(account.get("stale")))

    def _exposures(self, s, account, balance, snapshot, now, in_flight=()):
        """Venue positions are the source of truth for loss-at-risk; filled orders attribute them to agents.
        in_flight orders (see _in_flight) are added on top, at their actual cost if known."""
        orders = [o for o in s["orders"] if o.get("accountId") == account["id"]]
        attribution = {}
        for o in reversed(orders):  # orders are newest first, so the newest fill per market wins
            if o.get("filled"):
                attribution[o["market"]["id"]] = (o["agent"], o.get("governorEventGroup"))

        def group(market_id, known=None):
            book = snapshot.books.get(market_id)
            return known or (book.event_group if book else market_id)

        def pending(o):
            if o.get("committed") and o.get("status") == "submission unknown":
                return True  # budget reserved for a submission whose venue result is unknown
            if o.get("status") != "awaiting approval":
                return False
            approve_by = (o.get("governedTerms") or {}).get("approveBy")
            return approve_by is None or now <= dt.datetime.fromisoformat(approve_by)  # still approvable

        exposures = []
        for position in balance.get("positions") or []:
            quantity = to_decimal(position.get("position_fp", 0), "position_fp")
            amount = to_decimal(position.get("market_exposure_dollars", 0), "market_exposure_dollars")
            if quantity == ZERO or amount <= ZERO:
                continue
            ticker = position["ticker"]
            agent, known = attribution.get(ticker, ("external", None))
            exposures.append(Exposure(ticker, agent, group(ticker, known), "yes" if quantity > ZERO else "no", amount))
        for o in orders:
            if pending(o):
                market_id = o["market"]["id"]
                exposures.append(Exposure(market_id, o["agent"], group(market_id, o.get("governorEventGroup")),
                                          "yes" if o["direction"] == "Yes" else "no", to_decimal(o["amount"], "amount")))
        for o in in_flight:
            market_id = o["market"]["id"]
            exposures.append(Exposure(market_id, o["agent"], group(market_id, o.get("governorEventGroup")),
                                      "yes" if o["direction"] == "Yes" else "no",
                                      to_decimal(o.get("actualCost") or o["amount"], "amount")))
        return exposures

    def _resume(self, s):
        """Execute approved requests left without an order (e.g. a crash after a mid-batch save).

        Safe to repeat: the order's idempotency key is the governor proposal id, so an order created before
        the crash is returned rather than placed again. Expired proposals are skipped, never executed late.
        Each request's failure is contained to that request.
        """
        approved = [r for r in s.get("governorRequests", []) if r["status"] == "approved"]
        policy = self.policy_for(s) if approved else None
        for request in approved:
            if policy is None:
                request.update(status="executionFailed", note="Trading rules are not set for this workspace.")
                continue
            decision = next((d for d in s.get("governorDecisions", []) if d["proposalId"] == request["governorProposalId"]), None)
            account = next((a for a in s["accounts"] if a["id"] == request["accountId"]), None)
            if decision is None or account is None:
                request.update(status="executionFailed", note="The approved decision or its account is no longer available.")
                continue
            if self.clock() >= dt.datetime.fromisoformat(request["proposal"]["expiresAt"]):
                request.update(status="skipped", note="The proposal expired before execution could resume.")
                continue
            try:
                venue = self.workspace.venue(account)
                book = order_book(venue.get_orderbook(decision["marketId"]))
            except Exception:  # noqa: BLE001 - any venue fault skips this request only
                request.update(status="skipped", note="The market was unavailable when execution resumed.")
                continue
            self._execute_safely(s, request, SimpleNamespace(
                proposal_id=decision["proposalId"], agent_id=decision["agentId"], market_id=decision["marketId"],
                side=decision["side"], approved_budget=to_decimal(decision["approvedBudgetUsd"], "approvedBudgetUsd"),
                contracts=to_decimal(decision["contracts"], "contracts"),
                limit_price=to_decimal(decision["limitPrice"], "limitPrice")), venue, book, policy)

    def _execute_safely(self, s, request, decision, venue, book, policy):
        """_execute with any unexpected failure contained to this request, linking an order if one exists."""
        try:
            self._execute(s, request, decision, venue, book, policy)
        except Exception as exc:  # noqa: BLE001 - never let one order stop the batch or wedge later passes
            order_id = s["keys"].get(f"{request['agentId']}:governor:{request['governorProposalId']}")
            request.update(status="executionFailed",
                           note=f"Execution failed ({type(exc).__name__})." + (" Check the linked order." if order_id else ""))
            if order_id:
                request["orderId"] = order_id

    def _execute(self, s, request, decision, venue, book, policy):
        # The app authorizes whole cents, so round the approval up (by under a cent). The order itself buys exactly
        # the approved contracts as an immediate-or-cancel at the governor's limit price, so spending never exceeds
        # the approval and a market that moved above the limit simply leaves the order unfilled.
        amount = round_up(decision.approved_budget, CENT)
        if amount < 1:
            request.update(status="skipped", note="The approved stake is below the app's $1 order minimum.")
            return
        contracts, limit = decision.contracts, decision.limit_price
        if limit * contracts + taker_fee(contracts, limit, book.fee_multiplier) > amount:
            request.update(status="executionFailed", note="The approved contracts do not fit the authorized amount.")
            return  # cannot happen for allocator decisions, which are sized at or below this cost
        direction = "Yes" if decision.side == "yes" else "No"
        approve_by = (self.clock() + dt.timedelta(seconds=policy.approval_ttl_seconds)).isoformat()
        try:
            order = self.workspace.proposal(s, {
                "agentId": decision.agent_id, "marketId": decision.market_id, "direction": direction,
                "amount": float(amount), "reasoning": request["reasoning"],
                "idempotencyKey": f"governor:{decision.proposal_id}"},
                governed={"contracts": str(contracts), "limitPrice": str(limit), "approveBy": approve_by})
        except (ValueError, KeyError, StopIteration) as exc:
            request.update(status="executionFailed", note=(str(exc) or "Execution failed.")[:300])
            return
        order.update(governorProposalId=decision.proposal_id, governorEventGroup=book.event_group)
        request.update(status="submitted", orderId=order["id"])

    # Scoring: plan_scoring() under the lock, fetch_outcomes() unlocked, score_forecasts(s, outcomes) locked.

    def plan_scoring(self, s):
        """Per connected account, the markets with unscored forecasts by that account's agents."""
        accounts = {a["id"]: a for a in s["accounts"] if a.get("status") != "disconnected"}
        agent_account = {a["id"]: a["accountId"] for a in s["agentConfigs"] if a.get("accountId") in accounts}
        pending = {}
        for f in s.get("governorForecasts", []):
            if f["outcome"] is None and f["agentId"] in agent_account:
                pending.setdefault(agent_account[f["agentId"]], set()).add(f["marketId"])
        return {account_id: {"account": copy.deepcopy(accounts[account_id]), "markets": sorted(markets)}
                for account_id, markets in sorted(pending.items())}

    def fetch_outcomes(self, plans):
        """Settled results per account (market -> YES settlement value). Venue reads only; changes no state."""
        outcomes = {}
        for account_id, item in plans.items():
            try:
                venue = self.workspace.venue(item["account"])
            except (ValueError, KeyError):
                continue
            results = {}
            for market_id in item["markets"]:
                try:
                    value = venue.get_market_outcome(market_id)["outcome"]
                    if value is not None:
                        outcome = to_decimal(value, "outcome")
                        if ZERO <= outcome <= 1:
                            results[market_id] = outcome
                except (ValueError, KeyError):
                    continue  # unavailable or unreadable this time; try again on the next pass
            outcomes[account_id] = results
        return outcomes

    def score_forecasts(self, s, outcomes=None):
        """Resolve forecasts whose markets have settled. Returns how many were scored.

        Each account's results score that account's agents only, so demo and live results never mix. Every
        forecast is scored, including rejected proposals', which keeps reliability independent of the
        governor's own decisions.
        """
        if outcomes is None:
            outcomes = self.fetch_outcomes(self.plan_scoring(s))
        now = self.clock()
        scored = 0
        for account_id, results in sorted(outcomes.items()):
            agents = {a["id"] for a in s["agentConfigs"] if a.get("accountId") == account_id}
            if results:
                scored += records.resolve_forecasts(s, results, now, agents=agents)
        return scored

    # Operator controls and dashboard.

    def reset_breaker(self, s, account_id, reason, operator="Account owner"):
        account = next((a for a in s["accounts"] if a["id"] == account_id), None)
        if account is None:
            raise ValueError("Unknown account.")
        balance = self.workspace.refresh(s, account)
        if balance is None:
            raise ValueError("A current balance is needed to reset the loss breaker. Refresh and try again.")
        equity = _equity(balance)
        now = self.clock()
        state, audit = reset(records.breaker_state(s, account_id), operator=operator, reason=reason, equity=equity, now=now)
        records.save_breaker(s, account_id, state)
        records.record_breaker_event(s, account_id, audit, self.new_id())
        event(s, f"Loss breaker reset by {audit['operator']}: {audit['reason']}", "BREAKER")
        return state.to_dict()

    def action(self, s, b):
        kind = b.get("action")
        if kind == "governor-run-batch":
            return self.run_batch(s)
        if kind == "governor-score":
            return {"scored": self.score_forecasts(s)}
        if kind == "governor-reset-breaker":
            if not isinstance(b.get("accountId"), str) or not isinstance(b.get("reason"), str):
                raise ValueError("A breaker reset needs an accountId and a reason.")
            return self.reset_breaker(s, b["accountId"], b["reason"])
        raise ValueError("Unsupported governor action.")

    def summary(self, s):
        """Read model for the dashboard: small, with no batch inputs or decision traces."""
        policy = self.policy_for(s)
        return {
            "policy": policy.to_dict() if policy else None,
            "queued": sum(r["status"] == "queued" for r in s.get("governorRequests", [])),
            "breakers": {a["id"]: {**records.breaker_state(s, a["id"]).to_dict(), "accountName": a.get("name")}
                         for a in s["accounts"]},
            "breakerEvents": s.get("governorBreakerEvents", [])[:20],
            "reliability": {agent: {"score": str(r.score), "resolvedCount": r.resolved_count}
                            for agent, r in (records.reliability(s, policy).items() if policy else ())},
            "recentDecisions": [{k: v for k, v in d.items() if k != "trace"} for d in s.get("governorDecisions", [])[:50]],
            "recentRequests": self.views(s, s.get("governorRequests", [])[:50]),
        }
