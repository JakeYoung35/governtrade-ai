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
import datetime as dt
from types import SimpleNamespace
import uuid

from engine import event
from governor import api, records
from governor.allocator import decide
from governor.breaker import check, reset
from governor.fees import taker_fee
from governor.models import Exposure, Portfolio, Proposal
from governor.money import CENT, ZERO, round_up, to_decimal
from governor.policy import DEFAULT_POLICY
from governor.snapshot import build_snapshot, order_book

SCHEMA_PATH = "/api/governor/schema"


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def _invalid(errors):
    return 400, {"error": "Invalid proposal.", "errors": errors, "schema": SCHEMA_PATH}


class GovernorService:
    def __init__(self, workspace, policy=DEFAULT_POLICY, clock=utc_now, new_id=lambda: str(uuid.uuid4()),
                 find_request=None):
        """find_request(agent_id, proposal_id) returns an older stored request (Postgres) or None."""
        self.workspace, self.policy, self.clock, self.new_id = workspace, policy, clock, new_id
        self.find_request = find_request

    def schema(self):
        return api.describe(self.policy)

    # Submission and lookup.

    def submit(self, s, payload):
        """Validate and queue one proposal. Returns (http_status, body); 202 queued, 200 identical retry."""
        now = self.clock()
        normalized, errors = api.parse_submission(payload, received_at=now, policy=self.policy)
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

    def _view(self, s, request):
        decision = next((d for d in s.get("governorDecisions", []) if d["proposalId"] == request["governorProposalId"]), None)
        order = next((o for o in s["orders"] if o["id"] == request.get("orderId")), None) if request.get("orderId") else None
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

    # Batches.

    def run_batch(self, s):
        """Decide every queued request, one batch per account. Returns a summary per account."""
        s["clock"] = self.clock().isoformat()
        self._resume(s)
        queued = [r for r in s.get("governorRequests", []) if r["status"] == "queued"]
        return [self._run_account(s, account_id, sorted((r for r in queued if r["accountId"] == account_id),
                                                        key=lambda r: r["governorProposalId"]))
                for account_id in sorted({r["accountId"] for r in queued})]

    def _reject_all(self, items, note):
        for r in items:
            r.update(status="rejected", note=note)

    def _run_account(self, s, account_id, items):
        account = next((a for a in s["accounts"] if a["id"] == account_id), None)
        if account is None or account.get("status") == "disconnected":
            self._reject_all(items, "The agent's venue account is not connected.")
            return {"accountId": account_id, "decided": 0, "note": "account not connected"}
        live = {a["id"] for a in s["agentConfigs"] if a.get("status") == "active" and a.get("mandate")}
        self._reject_all([r for r in items if r["agentId"] not in live],
                         "The agent was paused or deleted before its proposal was decided.")
        items = [r for r in items if r["agentId"] in live]
        if not items:
            return {"accountId": account_id, "decided": 0, "note": "no live agents"}
        try:
            venue = self.workspace.venue(account)
        except (ValueError, KeyError):
            self._reject_all(items, "The venue account's credentials are unavailable.")
            return {"accountId": account_id, "decided": 0, "note": "credentials unavailable"}
        started = self.clock()
        balance = self.workspace.refresh(s, account)
        books = []
        for market_id in sorted({r["proposal"]["marketId"] for r in items}):
            try:
                raw = venue.get_orderbook(market_id)
                order_book(raw)  # validate now; an unusable book leaves the market out (marketNotInSnapshot)
            except ValueError:
                continue
            books.append(raw)
        snapshot_id = self.new_id()
        snapshot = build_snapshot(books, snapshot_id=snapshot_id, taken_at=started)
        now = self.clock()
        portfolio = self._portfolio(s, account, balance, snapshot, now)
        proposals = [records.proposal_from_dict(r["proposal"]) for r in items]
        reliability = records.reliability(s, self.policy)
        decisions = decide(proposals, snapshot, portfolio, reliability, now, self.policy)
        batch = records.batch_record(batch_id=self.new_id(), account_id=account_id, started_at=started, evaluated_at=now,
                                     snapshot_id=snapshot_id, books=books, proposals=proposals, portfolio=portfolio,
                                     reliability=reliability, policy=self.policy)
        records.record_batch(s, batch, decisions, now)
        by_id = {r["governorProposalId"]: r for r in items}
        # Record every outcome before sending any order: execution saves state (a crash-safe reservation),
        # and a request must never be saved as queued once its decision is recorded.
        for d in decisions:
            by_id[d.proposal_id].update(status="rejected" if d.outcome == "rejected" else "approved",
                                        batchId=batch["id"], decision=d.outcome)
        for d in decisions:
            if d.outcome != "rejected":
                self._execute(s, by_id[d.proposal_id], d, venue, snapshot.books[d.market_id])
        return {"accountId": account_id, "batchId": batch["id"], "decided": len(decisions)}

    def _portfolio(self, s, account, balance, snapshot, now):
        state = records.breaker_state(s, account["id"])
        paused = s["permissions"].get("automation") is False
        if balance is None:
            return Portfolio(ZERO, ZERO, (), paused=paused, breaker_latched=state.latched, valuation_stale=True)
        cash = to_decimal(balance["cash"], "cash")
        equity = cash + to_decimal(balance.get("reserved", 0), "reserved") + to_decimal(balance["positionValue"], "positionValue")
        baseline = account.get("baseline") or {}
        day_open = baseline.get("equity") if baseline.get("day") == now.date().isoformat() else None
        result = check(state, equity=equity, day_open_equity=day_open, now=now, policy=self.policy)
        if result.tripped:
            records.save_breaker(s, account["id"], result.state)
            records.record_breaker_event(s, account["id"], {
                "action": "breakerLatched", "at": now.isoformat(), "reason": result.state.reason, "equity": str(equity),
                "base": None if result.base is None else str(result.base)}, self.new_id())
            event(s, f"Loss breaker latched: {result.state.reason}", "BREAKER")
        return Portfolio(equity, max(ZERO, cash), self._exposures(s, account, balance, snapshot), paused=paused,
                         breaker_latched=result.state.latched, valuation_stale=bool(account.get("stale")))

    def _exposures(self, s, account, balance, snapshot):
        """Venue positions are the source of truth for loss-at-risk; filled orders attribute them to agents."""
        orders = [o for o in s["orders"] if o.get("accountId") == account["id"]]
        attribution = {}
        for o in reversed(orders):  # orders are newest first, so the newest fill per market wins
            if o.get("filled"):
                attribution[o["market"]["id"]] = (o["agent"], o.get("governorEventGroup"))

        def group(market_id, known=None):
            book = snapshot.books.get(market_id)
            return known or (book.event_group if book else market_id)

        exposures = []
        for position in balance.get("positions") or []:
            quantity = to_decimal(position.get("position_fp", 0), "position_fp")
            amount = to_decimal(position.get("market_exposure_dollars", 0), "market_exposure_dollars")
            if quantity == ZERO or amount <= ZERO:
                continue
            ticker = position["ticker"]
            agent, known = attribution.get(ticker, ("external", None))
            exposures.append(Exposure(ticker, agent, group(ticker, known), "yes" if quantity > ZERO else "no", amount))
        for o in orders:  # budget the user may still approve, or reserved for submissions with an unknown result
            if o.get("status") == "awaiting approval" or (o.get("committed") and o.get("status") == "submission unknown"):
                market_id = o["market"]["id"]
                exposures.append(Exposure(market_id, o["agent"], group(market_id, o.get("governorEventGroup")),
                                          "yes" if o["direction"] == "Yes" else "no", to_decimal(o["amount"], "amount")))
        return exposures

    def _resume(self, s):
        """Execute approved requests left without an order (e.g. a crash after a mid-batch save).

        Safe to repeat: the order's idempotency key is the governor proposal id, so an order created before
        the crash is returned rather than placed again. Expired proposals are skipped, never executed late.
        """
        for request in [r for r in s.get("governorRequests", []) if r["status"] == "approved"]:
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
            except (ValueError, KeyError):
                request.update(status="skipped", note="The market was unavailable when execution resumed.")
                continue
            self._execute(s, request, SimpleNamespace(
                proposal_id=decision["proposalId"], agent_id=decision["agentId"], market_id=decision["marketId"],
                side=decision["side"], approved_budget=to_decimal(decision["approvedBudgetUsd"], "approvedBudgetUsd"),
                contracts=to_decimal(decision["contracts"], "contracts"),
                limit_price=to_decimal(decision["limitPrice"], "limitPrice")), venue, book)

    def _execute(self, s, request, decision, venue, book):
        # The app authorizes whole cents, so round the approval up (by under a cent). The order itself buys exactly
        # the approved contracts at the limit price, so actual spending never exceeds the governor's approval.
        amount = round_up(decision.approved_budget, CENT)
        if amount < 1:
            request.update(status="skipped", note="The approved stake is below the app's $1 order minimum.")
            return
        # Safety net: the contracts at the limit price plus exact fees must fit the authorized amount.
        contracts, limit = decision.contracts, decision.limit_price
        while contracts > ZERO and limit * contracts + taker_fee(contracts, limit, book.fee_multiplier) > amount:
            contracts -= book.contract_step
        if contracts <= ZERO:
            request.update(status="skipped", note="No whole contract fits the cent-rounded approved stake.")
            return
        direction = "Yes" if decision.side == "yes" else "No"
        try:
            quote = venue.get_quote(decision.market_id, direction)
            price = to_decimal(quote["price"], "price")
        except (ValueError, KeyError):
            request.update(status="skipped", note="No fresh quote was available before execution.")
            return
        if not ZERO < price <= decision.limit_price:
            request.update(status="skipped", note=f"The price moved above the governor's limit of ${decision.limit_price}.")
            return
        try:
            order = self.workspace.proposal(s, {
                "agentId": decision.agent_id, "marketId": decision.market_id, "direction": direction,
                "amount": float(amount), "reasoning": request["reasoning"],
                "idempotencyKey": f"governor:{decision.proposal_id}"},
                governed={"contracts": str(contracts), "limitPrice": str(limit)})
        except (ValueError, KeyError, StopIteration) as exc:
            request.update(status="executionFailed", note=(str(exc) or "Execution failed.")[:300])
            return
        order.update(governorProposalId=decision.proposal_id, governorEventGroup=book.event_group)
        request.update(status="submitted", orderId=order["id"])

    # Scoring.

    def score_forecasts(self, s):
        """Resolve forecasts whose markets have settled. Returns how many were scored.

        Each account's venue reports outcomes for that account's agents only, so demo and live
        results never mix. Every forecast is scored, including rejected proposals', which keeps
        reliability independent of the governor's own decisions.
        """
        now = self.clock()
        accounts = {a["id"]: a for a in s["accounts"] if a.get("status") != "disconnected"}
        agent_account = {a["id"]: a["accountId"] for a in s["agentConfigs"] if a.get("accountId") in accounts}
        pending = {}
        for f in s.get("governorForecasts", []):
            if f["outcome"] is None and f["agentId"] in agent_account:
                pending.setdefault(agent_account[f["agentId"]], set()).add(f["marketId"])
        scored = 0
        for account_id in sorted(pending):
            try:
                venue = self.workspace.venue(accounts[account_id])
            except (ValueError, KeyError):
                continue
            outcomes = {}
            for market_id in sorted(pending[account_id]):
                try:
                    value = venue.get_market_outcome(market_id)["outcome"]
                    if value is None:
                        continue
                    outcome = to_decimal(value, "outcome")
                except (ValueError, KeyError):
                    continue  # unavailable or unreadable this time; try again on the next pass
                if ZERO <= outcome <= 1:
                    outcomes[market_id] = outcome
            agents = {agent for agent, account in agent_account.items() if account == account_id}
            scored += records.resolve_forecasts(s, outcomes, now, agents=agents) if outcomes else 0
        return scored

    # Operator controls and dashboard.

    def reset_breaker(self, s, account_id, reason, operator="Account owner"):
        account = next((a for a in s["accounts"] if a["id"] == account_id), None)
        if account is None:
            raise ValueError("Unknown account.")
        balance = self.workspace.refresh(s, account)
        if balance is None:
            raise ValueError("A current balance is needed to reset the loss breaker. Refresh and try again.")
        equity = (to_decimal(balance["cash"], "cash") + to_decimal(balance.get("reserved", 0), "reserved")
                  + to_decimal(balance["positionValue"], "positionValue"))
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
        return {
            "policy": self.policy.to_dict(),
            "queued": sum(r["status"] == "queued" for r in s.get("governorRequests", [])),
            "breakers": {a["id"]: {**records.breaker_state(s, a["id"]).to_dict(), "accountName": a.get("name")}
                         for a in s["accounts"]},
            "breakerEvents": s.get("governorBreakerEvents", [])[:20],
            "reliability": {agent: {"score": str(r.score), "resolvedCount": r.resolved_count}
                            for agent, r in records.reliability(s, self.policy).items()},
            "recentDecisions": [{k: v for k, v in d.items() if k != "trace"} for d in s.get("governorDecisions", [])[:50]],
            "recentRequests": [self._view(s, r) for r in s.get("governorRequests", [])[:50]],
        }
