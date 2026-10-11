"""Offline governor demo on recorded Kalshi order books: paper replay, no network, same result every run.

    python3 scripts/replay_demo.py           # print every beat
    python3 scripts/replay_demo.py --pause   # wait for Enter between beats while presenting

Drives the real GovernorService and Workspace (mandates, account ceiling, safety checks) against a
PaperVenue. Everything shown is simulated: fills, balances and settlements come from the paper ledger.
"""
import argparse
import copy
import datetime as dt
import itertools
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import main_seed  # noqa: E402
from governor import records  # noqa: E402
from governor.fees import fee_per_contract  # noqa: E402
from governor.money import to_decimal  # noqa: E402
from governor.policy import DEFAULT_POLICY  # noqa: E402
import trading_rules  # noqa: E402
from governor.sizing import target_stake  # noqa: E402
from governor_service import GovernorService  # noqa: E402
from replay.paper_venue import PaperVenue  # noqa: E402
from workspace import Workspace  # noqa: E402

CLE, PHI, HOU, PIT = ("KXNFLGAME-26OCT18BALCLE-CLE", "KXNFLGAME-26OCT18CARPHI-PHI",
                      "KXNFLGAME-26OCT18HOUJAC-HOU", "KXNFLGAME-26OCT18PITTB-PIT")
ACCOUNT = "paper-account"
AGENTS = {"reference": "Reference Agent", "momentum": "Momentum Agent", "aggressive": "Aggressive Agent"}
# $60 automatic, $300 daily and $100 reserve on the $1,000 paper account, as the shares the app stores.
MANDATE = {"autoLimitPercent": 6, "dailyCapPercent": 30, "minimumPercent": 10, "newMarketApproval": False}


class _NoCredentials:
    def get(self, reference):
        return {}  # the paper venue needs none


class Clock:
    """Real UTC time. The app's own checks (fresh quotes, UTC day boundaries) use the real clock, so paper
    quotes must not run ahead of it."""

    def now(self):
        return dt.datetime.now(dt.timezone.utc)


def build():
    clock = Clock()
    venue = PaperVenue(clock.now)
    s = main_seed()
    # This scripted demo saves the starting rules with a wider daily spending ceiling; its 3% daily loss limit is the
    # breaker the demo shows latching.
    trading_rules.fixture(s, {"dailySpendingPercent": 50})
    s["accounts"] = [{"id": ACCOUNT, "name": "Paper replay account", "venue": "kalshi", "environment": "sandbox",
                      "status": "connected", "credentialRef": "paper", "ceiling": {"dailyCap": 500}}]
    s["agentConfigs"] = [{"id": agent, "name": name, "accountId": ACCOUNT, "status": "active", "personality": None,
                          "mandate": copy.deepcopy(MANDATE), "modelValidated": True} for agent, name in AGENTS.items()]
    workspace = Workspace(_NoCredentials())
    workspace.venue = lambda account: venue
    ids = itertools.count(1)
    service = GovernorService(workspace, clock=clock.now, new_id=lambda: f"demo-{next(ids):04d}")
    return clock, venue, s, workspace, service


def proposal(agent, pid, market, side, p_yes, budget, reasoning):
    return {"schemaVersion": 1, "proposalId": pid, "agentId": agent, "marketId": market, "side": side,
            "estimatedYesProbability": p_yes, "maxBudgetUsd": budget, "reasoning": reasoning}


def money(value):
    return f"${float(value):,.2f}"


def run_demo(say=print, pause=lambda: None):
    clock, venue, s, workspace, service = build()
    account = s["accounts"][0]
    workspace.refresh(s, account)  # day-open equity
    results = {}

    def equity():
        b = account["balance"]
        return b["cash"] + b.get("reserved", 0) + b["positionValue"]

    def submit(payload):
        code, body = service.submit(s, payload)
        if code not in (200, 202):
            raise RuntimeError(f"Demo proposal refused: {body}")
        return body["governorProposalId"], code

    def show(gid):
        view = service.lookup(s, gid)
        d = view["decision"]
        order = view["order"]
        line = (f"  {AGENTS[view['agentId']]:<17} {view['side'].upper():<3} {view['marketId'][-3:]}  "
                f"{d['decision']:<8} stake {money(d['approvedBudgetUsd']):>8}  contracts {d['contracts']:>3}")
        if d["reasonCodes"]:
            line += f"  reasons: {', '.join(d['reasonCodes'])}"
        if order:
            line += f"  -> order {order['status']} ({order['quantity'] or 0:g} filled, cost {money(order['actualCost'] or 0)})"
        elif view["note"]:
            line += f"  -> {view['note']}"
        say(line)
        return view

    say("GovernTrade governor: offline replay demo (paper ledger, recorded Kalshi demo order books)")
    say(f"Bankroll {money(equity())}. Three agents propose; only the governor commits capital.\n")

    say("Beat 1. One batch, three agents competing for the same bankroll")
    pause()
    beat1 = [submit(proposal("reference", "ref-1", CLE, "yes", 0.50, 30, "Cleveland is undervalued at 36 cents."))[0],
             submit(proposal("momentum", "mom-1", CLE, "no", 0.25, 30, "Momentum favors Baltimore."))[0],
             submit(proposal("aggressive", "agg-1", PHI, "yes", 0.80, 200, "Philadelphia should win comfortably."))[0]]
    service.run_batch(s)
    results["beat1"] = {gid: show(gid) for gid in beat1}
    say("  The momentum agent lost the Cleveland conflict to the stronger edge; the aggressive request was resized by the 5% market cap.\n")

    say("Beat 2. An identical retry does not trade twice")
    pause()
    orders_before = len(s["orders"])
    gid, code = submit(proposal("reference", "ref-1", CLE, "yes", 0.50, 30, "Cleveland is undervalued at 36 cents."))
    service.run_batch(s)
    results["beat2"] = {"code": code, "sameProposal": gid == beat1[0], "newOrders": len(s["orders"]) - orders_before}
    say(f"  HTTP {code}: same governor proposal returned, {results['beat2']['newOrders']} new orders.\n")

    say("Beat 3. A price crash latches the daily loss breaker")
    pause()
    venue.set_price(CLE, yes_bid="0.02", no_bid="0.97")
    venue.set_price(PHI, yes_bid="0.05", no_bid="0.94")
    workspace.refresh(s, account)
    say(f"  Marked equity falls to {money(equity())} (day open {money(account['baseline']['equity'])}).")
    blocked = submit(proposal("momentum", "mom-2", HOU, "yes", 0.55, 20, "Houston is mispriced."))[0]
    service.run_batch(s)
    results["beat3"] = {"view": show(blocked), "latched": records.breaker_state(s, ACCOUNT).latched,
                        "reason": records.breaker_state(s, ACCOUNT).reason}
    say(f"  Breaker: {results['beat3']['reason']}\n")

    say("Beat 4. An operator reviews the loss and resets the breaker")
    pause()
    service.action(s, {"action": "governor-reset-breaker", "accountId": ACCOUNT, "reason": "Reviewed the crash; resuming with care."})
    resumed = submit(proposal("momentum", "mom-3", HOU, "yes", 0.55, 20, "Houston is mispriced."))[0]
    service.run_batch(s)
    results["beat4"] = {"latched": records.breaker_state(s, ACCOUNT).latched, "view": show(resumed)}
    say("  The reset is audited, and trading resumes measured from the equity at reset.\n")

    say("Beat 5. Markets settle, forecasts are scored, and reliability changes the next stake")
    pause()
    before = {agent: records.reliability(s).get(agent) for agent in AGENTS}
    venue.settle(CLE, "0")
    venue.settle(PHI, "1")
    scored = service.score_forecasts(s)
    after = records.reliability(s)
    for agent, name in AGENTS.items():
        old = before[agent].score if before[agent] else None
        new = after[agent]
        say(f"  {name:<17} reliability {float(old):.3f} -> {float(new.score):.3f}  "
            f"({new.resolved_count} resolved, Brier {float(new.brier) if new.brier is not None else 0:.3f})")
    # The governor's own sizing for one identical call, at each agent's updated reliability (a what-if, not an order).
    workspace.refresh(s, account)
    ask = to_decimal(venue.get_quote(PIT, "Yes")["price"], "price")
    cost = ask + fee_per_contract(ask)
    targets = {}
    for agent in AGENTS:
        targets[agent] = target_stake(equity=to_decimal(equity(), "equity"), win_probability=to_decimal("0.66"),
                                      effective_cost=cost, reliability=after[agent].score,
                                      requested_budget=to_decimal(100), kelly_fraction=DEFAULT_POLICY.kelly_fraction)
        say(f"  {AGENTS[agent]:<17} same Pittsburgh YES call at 66%: Kelly target {money(targets[agent])}")
    results["beat5"] = {"scored": scored, "reliability": {a: after[a].score for a in AGENTS}, "targets": targets}

    workspace.refresh(s, account)
    say(f"\nFinal paper equity {money(equity())}. Every decision above has a stored batch that replays exactly.")
    results["replays"] = all([d.to_dict() for d in records.replay(b)] ==
                             [{k: v for k, v in d.items() if k not in ("batchId", "accountId", "decidedAt")}
                              for d in sorted((d for d in s["governorDecisions"] if d["batchId"] == b["id"]),
                                              key=lambda d: d["proposalId"])]
                             for b in s["governorBatches"])
    say(f"Replay check: {'all batches reproduce their decisions' if results['replays'] else 'MISMATCH'}.")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pause", action="store_true", help="wait for Enter between beats")
    args = parser.parse_args()
    run_demo(pause=(lambda: input("    [Enter] ")) if args.pause else (lambda: None))


if __name__ == "__main__":
    main()
