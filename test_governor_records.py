"""Step 6 and 7: governor records in workspace state, exact replay, and the loss breaker."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import copy
import json
import unittest

from engine import main_seed, migrate
from governor import records, rules
from governor.allocator import decide
from governor.breaker import BreakerState, check, reset
from governor.models import Exposure, Portfolio, Proposal
from governor.policy import DEFAULT_POLICY, Policy
from governor.reliability import ReliabilityScore, score_all
from governor.snapshot import build_snapshot

NOW = datetime(2026, 10, 10, 14, 30, tzinfo=timezone.utc)
ACCOUNT = "acct-1"


def book(market_id, *, event="GAME", yes_ask="0.37", no_ask="0.64", depth="500"):
    """A get_orderbook payload whose asks are yes_ask / no_ask."""
    return {"id": market_id, "name": market_id, "eventTicker": event, "agent": "sports", "venue": "kalshi",
            "open": True, "executionReady": True, "feeMultiplier": 1.0, "closeAt": "2026-10-20T17:00:00Z",
            "yesBids": [[str(D(1) - D(no_ask)), depth]], "noBids": [[str(D(1) - D(yes_ask)), depth]],
            "asOf": NOW.isoformat()}


def proposal(pid, *, agent="a1", market="M1", side="yes", p="0.45", budget="25"):
    return Proposal(pid, agent, market, side, D(p), D(budget), NOW - timedelta(seconds=1), NOW + timedelta(seconds=60))


def batch_inputs():
    books = [book("M1"), book("M2", event="OTHER", yes_ask="0.40", no_ask="0.62")]
    proposals = [proposal("p3", market="M1", p="0.45"), proposal("p1", agent="a2", market="M1", side="no", p="0.30"),
                 proposal("p2", agent="a3", market="M2", p="0.55", budget="40")]
    portfolio = Portfolio(D(1000), D(900), [Exposure("X", "a1", "OTHER", "yes", D(20))])
    reliability = {"a1": ReliabilityScore("a1", D("0.62"), 12, D("0.09"))}
    return books, proposals, portfolio, reliability


def run_batch(s, batch_id="b1"):
    books, proposals, portfolio, reliability = batch_inputs()
    snapshot = build_snapshot(books, snapshot_id=f"snap-{batch_id}", taken_at=NOW)
    decisions = decide(proposals, snapshot, portfolio, reliability, NOW)
    batch = records.batch_record(batch_id=batch_id, account_id=ACCOUNT, started_at=NOW, evaluated_at=NOW,
                                 snapshot_id=f"snap-{batch_id}", books=books, proposals=proposals,
                                 portfolio=portfolio, reliability=reliability)
    records.record_batch(s, batch, decisions, NOW)
    return batch, decisions


class BreakerTests(unittest.TestCase):
    def test_latches_at_exactly_the_daily_loss_limit(self):
        self.assertFalse(check(BreakerState(), equity=D("970.01"), day_open_equity=D(1000), now=NOW).tripped)
        result = check(BreakerState(), equity=D("970.00"), day_open_equity=D(1000), now=NOW)
        self.assertTrue(result.tripped)
        self.assertEqual((result.base, result.threshold), (D(1000), D("970.00")))
        self.assertTrue(result.state.latched)
        self.assertEqual(result.state.latched_at, NOW)
        self.assertIn("3.00%", result.state.reason)

    def test_stays_latched_through_recovery_and_a_new_day(self):
        latched = check(BreakerState(), equity=D(960), day_open_equity=D(1000), now=NOW).state
        tomorrow = NOW + timedelta(days=1)
        for equity, base in ((D(2000), D(1000)), (D(900), D(900)), (None, None)):
            result = check(latched, equity=equity, day_open_equity=base, now=tomorrow)
            self.assertTrue(result.state.latched)
            self.assertFalse(result.tripped)

    def test_unmeasurable_inputs_never_latch_or_clear(self):
        for equity, base in ((None, D(1000)), (D(500), None), (D(500), D(0))):
            result = check(BreakerState(), equity=equity, day_open_equity=base, now=NOW)
            self.assertFalse(result.state.latched)

    def test_reset_requires_operator_reason_and_valuation(self):
        latched = check(BreakerState(), equity=D(960), day_open_equity=D(1000), now=NOW).state
        with self.assertRaises(ValueError):
            reset(BreakerState(), operator="ops", reason="ok", equity=D(960), now=NOW)
        for kwargs in (dict(operator=" ", reason="ok", equity=D(960)), dict(operator="ops", reason="", equity=D(960)),
                       dict(operator="ops", reason="ok", equity=D(0))):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                reset(latched, now=NOW, **kwargs)
        state, audit = reset(latched, operator="charlie", reason="Reviewed the loss", equity=D(960), now=NOW)
        self.assertFalse(state.latched)
        self.assertEqual((audit["operator"], audit["reason"], audit["equityAtReset"]), ("charlie", "Reviewed the loss", "960"))
        self.assertEqual(audit["latchReason"], latched.reason)

    def test_reset_rebases_for_the_rest_of_the_day_only(self):
        latched = check(BreakerState(), equity=D(960), day_open_equity=D(1000), now=NOW).state
        state, _ = reset(latched, operator="ops", reason="accepted", equity=D(960), now=NOW)
        later = NOW + timedelta(hours=1)
        # Still below the original day-open threshold, but measured from the reset value now.
        self.assertFalse(check(state, equity=D(940), day_open_equity=D(1000), now=later).tripped)
        self.assertTrue(check(state, equity=D("931.20"), day_open_equity=D(1000), now=later).tripped)
        tomorrow = NOW + timedelta(days=1)
        self.assertFalse(check(state, equity=D(940), day_open_equity=D(940), now=tomorrow).tripped)

    def test_state_round_trips_through_json(self):
        latched = check(BreakerState(), equity=D(960), day_open_equity=D(1000), now=NOW).state
        for state in (BreakerState(), latched, reset(latched, operator="o", reason="r", equity=D(960), now=NOW)[0]):
            self.assertEqual(BreakerState.from_dict(json.loads(json.dumps(state.to_dict()))), state)

    def test_latched_breaker_blocks_every_proposal(self):
        books, proposals, _, _ = batch_inputs()
        snapshot = build_snapshot(books, snapshot_id="s", taken_at=NOW)
        decisions = decide(proposals, snapshot, Portfolio(D(1000), D(1000), breaker_latched=True), {}, NOW)
        self.assertTrue(all(d.reason_codes == (rules.LOSS_BREAKER,) for d in decisions))


class BatchRecordTests(unittest.TestCase):
    def test_stored_batch_replays_to_identical_decisions_after_json_round_trip(self):
        s = main_seed()
        batch, decisions = run_batch(s)
        saved = json.loads(json.dumps(s, allow_nan=False))  # what server.save writes to disk
        stored_batch = saved["governorBatches"][0]
        self.assertEqual([d.to_dict() for d in records.replay(stored_batch)], [d.to_dict() for d in decisions])
        stored = {d["proposalId"]: d for d in saved["governorDecisions"]}
        for d in decisions:
            self.assertEqual({k: v for k, v in stored[d.proposal_id].items() if k not in ("batchId", "accountId", "decidedAt")},
                             d.to_dict())
            self.assertEqual((stored[d.proposal_id]["batchId"], stored[d.proposal_id]["accountId"]), ("b1", ACCOUNT))

    def test_replay_detects_any_tampered_input(self):
        s = main_seed()
        batch, decisions = run_batch(s)
        tampered = copy.deepcopy(batch)
        tampered["inputs"]["portfolio"]["availableCash"] = "5"
        self.assertNotEqual([d.to_dict() for d in records.replay(tampered)], [d.to_dict() for d in decisions])

    def test_policy_round_trips(self):
        custom = Policy(version="v2", market_cap="0.08", max_quote_age_seconds=45)
        self.assertEqual(Policy.from_dict(json.loads(json.dumps(custom.to_dict()))), custom)
        with self.assertRaisesRegex(ValueError, "Unknown policy"):
            Policy.from_dict({**DEFAULT_POLICY.to_dict(), "surprise": 1})

    def test_records_are_write_once(self):
        s = main_seed()
        run_batch(s)
        with self.assertRaisesRegex(ValueError, "already recorded"):
            run_batch(s, "b1")
        with self.assertRaisesRegex(ValueError, "already has a recorded decision"):
            run_batch(s, "b2")

    def test_newest_first_with_descending_ids_within_a_batch_and_bounded_windows(self):
        s = main_seed()
        run_batch(s)
        self.assertEqual([d["proposalId"] for d in s["governorDecisions"]], ["p3", "p2", "p1"])
        s["governorBatches"] = [{"id": f"old{i}"} for i in range(records.BATCH_WINDOW)]
        s["governorDecisions"] = [{"proposalId": f"old{i}"} for i in range(records.DECISION_WINDOW)]
        run_batch(s, "b9")
        self.assertEqual(len(s["governorBatches"]), records.BATCH_WINDOW)
        self.assertEqual(s["governorBatches"][0]["id"], "b9")
        self.assertEqual(len(s["governorDecisions"]), records.DECISION_WINDOW)
        self.assertEqual(s["governorDecisions"][0]["proposalId"], "p3")


class ForecastTests(unittest.TestCase):
    def test_first_forecast_per_agent_and_market_only(self):
        s = main_seed()
        self.assertTrue(records.record_forecast(s, proposal("p1", p="0.40")))
        self.assertFalse(records.record_forecast(s, proposal("p2", p="0.90")))
        self.assertTrue(records.record_forecast(s, proposal("p3", agent="a2", p="0.20")))
        self.assertEqual([f["proposalId"] for f in s["governorForecasts"]], ["p1", "p3"])

    def test_resolution_feeds_reliability(self):
        s = main_seed()
        records.record_forecast(s, proposal("p1", p="0.90"))
        records.record_forecast(s, proposal("p2", market="M2", p="0.20"))
        self.assertEqual(records.reliability(s)["a1"].resolved_count, 0)
        self.assertEqual(records.resolve_forecasts(s, {"M1": 1, "M9": 0}, NOW), 1)
        self.assertEqual(records.resolve_forecasts(s, {"M1": 0}, NOW), 0)  # already resolved; never rewritten
        score = records.reliability(s)["a1"]
        self.assertEqual((score.resolved_count, score.brier), (1, D("0.01")))
        self.assertEqual(score, score_all(records.forecasts(s))["a1"])
        self.assertEqual(s["governorForecasts"][0]["resolvedAt"], NOW.isoformat())
        with self.assertRaises(ValueError):
            records.resolve_forecasts(s, {"M2": 2}, NOW)


class StateTests(unittest.TestCase):
    def test_breaker_state_and_audit_in_workspace_state(self):
        s = main_seed()
        self.assertEqual(records.breaker_state(s, ACCOUNT), BreakerState())
        latched = check(BreakerState(), equity=D(960), day_open_equity=D(1000), now=NOW).state
        records.save_breaker(s, ACCOUNT, latched)
        self.assertEqual(records.breaker_state(json.loads(json.dumps(s)), ACCOUNT), latched)
        for i in range(records.BREAKER_EVENT_WINDOW + 5):
            records.record_breaker_event(s, ACCOUNT, {"action": "breakerReset", "n": i})
        events = s["governor"]["breakerEvents"]
        self.assertEqual((len(events), events[0]["n"], events[0]["accountId"]),
                         (records.BREAKER_EVENT_WINDOW, records.BREAKER_EVENT_WINDOW + 4, ACCOUNT))

    def test_seed_and_migration_include_governor_records(self):
        s = main_seed()
        for key in ("governor", "governorBatches", "governorDecisions", "governorForecasts"):
            self.assertIn(key, s)
        old = {k: v for k, v in main_seed().items() if not k.startswith("governor")}
        migrated = migrate(old)
        self.assertEqual(migrated["governor"], {"breakers": {}, "breakerEvents": []})
        kept = main_seed()
        kept["governorForecasts"] = [{"proposalId": "keep"}]
        self.assertEqual(migrate(kept)["governorForecasts"], [{"proposalId": "keep"}])


if __name__ == "__main__":
    unittest.main()
