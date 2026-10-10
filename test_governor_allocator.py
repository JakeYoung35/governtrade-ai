"""Batch allocation: eligibility, ranking, conflicts, caps, depth and determinism."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import json
import random
import unittest

from governor import rules
from governor.allocator import decide
from governor.fees import taker_fee
from governor.models import Exposure, OrderBook, Portfolio, PriceLevel, Proposal, Snapshot
from governor.reliability import ReliabilityScore

NOW = datetime(2026, 10, 10, 14, 0, tzinfo=timezone.utc)


def book(market_id, *, yes_asks=(), no_asks=(), event=None, taken_at=NOW, is_open=True):
    """Build a book from the asks a buyer sees; stored as the opposing bids Kalshi reports."""
    return OrderBook(market_id, event or market_id, taken_at,
                     yes_bids=[PriceLevel(D(1) - D(p), q) for p, q in no_asks],
                     no_bids=[PriceLevel(D(1) - D(p), q) for p, q in yes_asks], is_open=is_open)


def snapshot(*books):
    return Snapshot("snap-1", NOW, {b.market_id: b for b in books})


def proposal(pid, *, agent="a1", market="M1", side="yes", p="0.65", budget="100", ceiling=None, ttl=60):
    return Proposal(pid, agent, market, side, D(p), D(budget), NOW - timedelta(seconds=1),
                    NOW + timedelta(seconds=ttl), D(ceiling) if ceiling else None)


def portfolio(equity="1000", cash=None, exposures=(), **flags):
    return Portfolio(D(equity), D(cash or equity), tuple(exposures), **flags)


def scores(**by_agent):
    return {agent: ReliabilityScore(agent, D(score), 10, D("0.1")) for agent, score in by_agent.items()}


def run(proposals, snap, port=None, reliability=None):
    return {d.proposal_id: d for d in decide(proposals, snap, port or portfolio(), reliability or {}, NOW)}


class SizingAndAcceptance(unittest.TestCase):
    def test_kelly_target_becomes_whole_contracts_at_exact_cost(self):
        d = run([proposal("p1")], snapshot(book("M1", yes_asks=[("0.50", "1000")])), reliability=scores(a1="0.6"))["p1"]
        # effective cost 0.50 + 0.0175 fee; target = 1000 * 0.25 * 0.6 * (0.65 - 0.5175) / (1 - 0.5175)
        target = D(1000) * D("0.25") * D("0.6") * ((D("0.65") - D("0.5175")) / (D(1) - D("0.5175")))
        self.assertEqual(D(d.trace["targetStake"]), target)
        self.assertEqual((d.outcome, d.reason_codes, d.contracts, d.limit_price), ("approved", (), D(79), D("0.50")))
        self.assertEqual(d.approved_budget, D("39.5") + taker_fee(D(79), D("0.50")))
        self.assertLessEqual(d.approved_budget, target)

    def test_unknown_agent_gets_prior_reliability(self):
        d = run([proposal("p1")], snapshot(book("M1", yes_asks=[("0.50", "1000")])))["p1"]
        self.assertEqual(d.reliability, D("0.5"))

    def test_no_side_buys_from_no_asks(self):
        d = run([proposal("p1", side="no", p="0.30")], snapshot(book("M1", no_asks=[("0.55", "1000")])))["p1"]
        self.assertEqual((d.outcome, d.side, d.limit_price), ("approved", "no", D("0.55")))

    def test_decision_serializes_to_json(self):
        d = run([proposal("p1")], snapshot(book("M1", yes_asks=[("0.50", "1000")])))["p1"]
        payload = json.loads(json.dumps(d.to_dict()))
        self.assertEqual(payload["decision"], "approved")
        self.assertEqual(payload["policyVersion"], "governor-policy-v1")


class CapsAndLiquidity(unittest.TestCase):
    def test_market_cap_resizes(self):
        d = run([proposal("p1", p="0.9", budget="500")], snapshot(book("M1", yes_asks=[("0.50", "10000")])),
                reliability=scores(a1="1"))["p1"]
        self.assertEqual(d.outcome, "resized")
        self.assertEqual(d.reason_codes[0], rules.MARKET_CAP)
        self.assertLessEqual(d.approved_budget, D(50))
        self.assertGreater(d.approved_budget, D(49))

    def test_cash_limit(self):
        d = run([proposal("p1", p="0.9", budget="500")], snapshot(book("M1", yes_asks=[("0.50", "10000")])),
                port=portfolio(cash="20"), reliability=scores(a1="1"))["p1"]
        self.assertEqual(d.reason_codes[0], rules.CASH_LIMIT)
        self.assertLessEqual(d.approved_budget, D(20))

    def test_existing_exposure_counts_toward_agent_and_total_caps(self):
        held = [Exposure(f"X{i}", "a1", f"E{i}", "yes", D(49)) for i in range(3)]  # 147 of a1's 150
        d = run([proposal("p1", p="0.9", budget="500")], snapshot(book("M1", yes_asks=[("0.50", "10000")])),
                port=portfolio(exposures=held), reliability=scores(a1="1"))["p1"]
        self.assertEqual(d.reason_codes[0], rules.AGENT_CAP)
        self.assertLessEqual(d.approved_budget, D(3))
        others = [Exposure(f"Y{i}", f"b{i}", f"F{i}", "yes", D(49)) for i in range(6)]  # 294 of 300 total
        d = run([proposal("p1", p="0.9", budget="500")], snapshot(book("M1", yes_asks=[("0.50", "10000")])),
                port=portfolio(exposures=others), reliability=scores(a1="1"))["p1"]
        self.assertEqual(d.reason_codes[0], rules.TOTAL_CAP)
        self.assertLessEqual(d.approved_budget, D(6))

    def test_event_group_cap_spans_markets_within_one_batch(self):
        snap = snapshot(book("M1", yes_asks=[("0.50", "10000")], event="GAME"),
                        book("M2", yes_asks=[("0.50", "10000")], event="GAME"))
        result = run([proposal("p1", market="M1", p="0.9", budget="500"),
                      proposal("p2", agent="a2", market="M2", p="0.9", budget="500")],
                     snap, reliability=scores(a1="1", a2="1"))
        total = result["p1"].approved_budget + result["p2"].approved_budget
        self.assertLessEqual(total, D(100))
        self.assertIn(rules.EVENT_GROUP_CAP, result["p2"].reason_codes)

    def test_walks_ladder_and_reports_depth(self):
        snap = snapshot(book("M1", yes_asks=[("0.50", "5"), ("0.52", "5")]))
        d = run([proposal("p1", p="0.9")], snap, reliability=scores(a1="1"))["p1"]
        self.assertEqual((d.contracts, d.limit_price), (D(10), D("0.52")))
        self.assertIn(rules.NO_LIQUIDITY, d.reason_codes)
        self.assertEqual(d.approved_budget, D("2.5") + taker_fee(D(5), D("0.50")) + D("2.6") + taker_fee(D(5), D("0.52")))

    def test_price_ceiling_stops_the_walk(self):
        snap = snapshot(book("M1", yes_asks=[("0.50", "5"), ("0.52", "5")]))
        d = run([proposal("p1", p="0.9", ceiling="0.50")], snap, reliability=scores(a1="1"))["p1"]
        self.assertEqual((d.contracts, d.limit_price), (D(5), D("0.50")))

    def test_budget_below_one_contract_is_zero_size(self):
        d = run([proposal("p1", budget="0.30")], snapshot(book("M1", yes_asks=[("0.50", "1000")])))["p1"]
        self.assertEqual((d.outcome, d.reason_codes, d.contracts), ("rejected", (rules.ZERO_SIZE,), D(0)))


class Conflicts(unittest.TestCase):
    def test_opposite_sides_one_winner_by_reliability_times_edge(self):
        snap = snapshot(book("M1", yes_asks=[("0.52", "1000")], no_asks=[("0.50", "1000")]))
        result = run([proposal("yes", agent="a1", p="0.70"), proposal("no", agent="a2", side="no", p="0.20")],
                     snap, reliability=scores(a1="0.6", a2="0.6"))
        self.assertNotEqual(result["no"].outcome, "rejected")
        self.assertEqual(result["yes"].reason_codes, (rules.CONFLICT,))
        self.assertEqual(result["yes"].trace["lostTo"], "no")

    def test_reliability_can_flip_the_winner(self):
        snap = snapshot(book("M1", yes_asks=[("0.52", "1000")], no_asks=[("0.50", "1000")]))
        result = run([proposal("yes", agent="a1", p="0.70"), proposal("no", agent="a2", side="no", p="0.20")],
                     snap, reliability=scores(a1="0.9", a2="0.2"))
        self.assertNotEqual(result["yes"].outcome, "rejected")
        self.assertEqual(result["no"].reason_codes, (rules.CONFLICT,))

    def test_same_side_is_redundant(self):
        result = run([proposal("p1", agent="a1", p="0.70"), proposal("p2", agent="a2", p="0.65")],
                     snapshot(book("M1", yes_asks=[("0.50", "1000")])))
        self.assertEqual(result["p1"].outcome, "approved")
        self.assertEqual(result["p2"].reason_codes, (rules.REDUNDANT,))

    def test_zero_size_winner_does_not_take_the_market(self):
        result = run([proposal("p1", agent="a1", p="0.90", budget="0.30"), proposal("p2", agent="a2", p="0.70")],
                     snapshot(book("M1", yes_asks=[("0.50", "1000")])))
        self.assertEqual(result["p1"].reason_codes, (rules.ZERO_SIZE,))
        self.assertEqual(result["p2"].outcome, "approved")


class Eligibility(unittest.TestCase):
    def assertReason(self, proposals, snap, reason, port=None):
        for d in run(proposals, snap, port).values():
            self.assertEqual((d.outcome, d.reason_codes, d.approved_budget), ("rejected", (reason,), D(0)))

    def test_each_rule(self):
        good = book("M1", yes_asks=[("0.50", "1000")])
        self.assertReason([proposal("p1", market="NOPE")], snapshot(good), rules.MARKET_NOT_IN_SNAPSHOT)
        self.assertReason([proposal("p1")], snapshot(book("M1", yes_asks=[("0.5", "9")], is_open=False)), rules.MARKET_CLOSED)
        self.assertReason([Proposal("p1", "a1", "M1", "yes", D("0.65"), D(100), NOW - timedelta(seconds=90),
                                    NOW - timedelta(seconds=30))], snapshot(good), rules.PROPOSAL_EXPIRED)
        self.assertReason([proposal("p1")], snapshot(book("M1", yes_asks=[("0.5", "9")], taken_at=NOW - timedelta(seconds=31))), rules.STALE_QUOTE)
        self.assertReason([proposal("p1")], snapshot(book("M1", yes_asks=[("0.5", "9")], taken_at=NOW + timedelta(seconds=10))), rules.STALE_QUOTE)
        self.assertReason([proposal("p1")], snapshot(good), rules.POSITION_OPEN,
                          portfolio(exposures=[Exposure("M1", "other", "M1", "no", D(5))]))
        self.assertReason([proposal("p1")], snapshot(book("M1", no_asks=[("0.5", "9")])), rules.NO_LIQUIDITY)
        self.assertReason([proposal("p1", ceiling="0.45")], snapshot(good), rules.PRICE_CEILING)
        self.assertReason([proposal("p1", p="0.51")], snapshot(good), rules.NO_EDGE)

    def test_quote_exactly_at_max_age_is_fresh(self):
        d = run([proposal("p1")], snapshot(book("M1", yes_asks=[("0.5", "1000")], taken_at=NOW - timedelta(seconds=30))))["p1"]
        self.assertEqual(d.outcome, "approved")

    def test_portfolio_gates_block_everything_in_priority_order(self):
        snap = snapshot(book("M1", yes_asks=[("0.50", "1000")]))
        self.assertReason([proposal("p1")], snap, rules.PAUSED, portfolio(paused=True, breaker_latched=True))
        self.assertReason([proposal("p1")], snap, rules.LOSS_BREAKER, portfolio(breaker_latched=True))
        self.assertReason([proposal("p1")], snap, rules.STALE_VALUATION, portfolio(valuation_stale=True))
        self.assertReason([proposal("p1")], snap, rules.NO_EQUITY, portfolio(equity="0", cash="0"))


class BatchInvariants(unittest.TestCase):
    def batch(self):
        books = [book(f"M{i}", yes_asks=[("0.40", "300"), ("0.45", "300")], no_asks=[("0.55", "300")],
                      event=f"E{i % 3}") for i in range(8)]
        proposals = [proposal(f"p{i:02d}", agent=f"a{i % 4}", market=f"M{i % 8}", side="yes" if i % 3 else "no",
                              p=str(D("0.55") + D(i % 7) / 20 if i % 3 else D("0.30") - D(i % 5) / 50), budget="120")
                     for i in range(24)]
        return proposals, snapshot(*books), scores(a0="0.4", a1="0.7", a2="0.55", a3="0.9")

    def test_shuffled_input_gives_identical_decisions(self):
        proposals, snap, rel = self.batch()
        expected = [d.to_dict() for d in decide(proposals, snap, portfolio(), rel, NOW)]
        for seed in range(5):
            shuffled = proposals[:]
            random.Random(seed).shuffle(shuffled)
            self.assertEqual([d.to_dict() for d in decide(shuffled, snap, portfolio(), rel, NOW)], expected)

    def test_batch_respects_every_cap_and_one_winner_per_market(self):
        proposals, snap, rel = self.batch()
        port = portfolio(equity="1000", cash="250")
        funded = [d for d in decide(proposals, snap, port, rel, NOW) if d.outcome != "rejected"]
        self.assertTrue(funded)
        self.assertLessEqual(sum(d.approved_budget for d in funded), D(250))
        self.assertLessEqual(sum(d.approved_budget for d in funded), D(300))
        markets = [d.market_id for d in funded]
        self.assertEqual(len(markets), len(set(markets)))
        for key, cap in ((lambda d: d.market_id, D(50)), (lambda d: d.agent_id, D(150)),
                         (lambda d: snap.books[d.market_id].event_group, D(100))):
            totals = {}
            for d in funded:
                totals[key(d)] = totals.get(key(d), D(0)) + d.approved_budget
            self.assertTrue(all(total <= cap for total in totals.values()), totals)

    def test_duplicate_proposal_ids_are_refused(self):
        with self.assertRaises(ValueError):
            decide([proposal("p1"), proposal("p1")], snapshot(), portfolio(), {}, NOW)


if __name__ == "__main__":
    unittest.main()
