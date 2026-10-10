"""Governor building blocks: money, fees, models, policy, sizing and reliability."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import unittest

from governor.fees import fee_per_contract, taker_fee
from governor.models import OrderBook, PriceLevel, Proposal
from governor.money import round_down, round_up, to_decimal
from governor.policy import DEFAULT_POLICY, Policy
from governor.reliability import Forecast, score_agent, score_all
from governor.sizing import full_kelly, target_stake

NOW = datetime(2026, 10, 10, 14, 0, tzinfo=timezone.utc)


def proposal(**overrides):
    values = dict(proposal_id="p1", agent_id="a1", market_id="M1", side="yes",
                  estimated_yes_probability="0.65", requested_budget="100",
                  received_at=NOW, expires_at=NOW + timedelta(seconds=60))
    values.update(overrides)
    return Proposal(**values)


class MoneyTests(unittest.TestCase):
    def test_floats_convert_by_their_shortest_repr(self):
        self.assertEqual(to_decimal(0.1), D("0.1"))
        self.assertEqual(to_decimal(10), D("10"))

    def test_rejects_non_numbers_and_non_finite(self):
        for bad in (True, None, "abc", float("nan"), float("inf"), "Infinity", [1]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                to_decimal(bad)

    def test_rounding_to_steps(self):
        self.assertEqual(round_down(D("78.9"), D("1")), D("78"))
        self.assertEqual(round_up(D("0.016317"), D("0.0001")), D("0.0164"))
        self.assertEqual(round_up(D("0.0164"), D("0.0001")), D("0.0164"))


class FeeTests(unittest.TestCase):
    def test_matches_live_demo_fills(self):
        # Observed on Kalshi demo 2026-10-10.
        self.assertEqual(taker_fee(D(1), D("0.37")), D("0.0164"))
        self.assertEqual(taker_fee(D(1), D("0.36")), D("0.0162"))

    def test_scales_with_count_and_multiplier(self):
        self.assertEqual(taker_fee(D(100), D("0.5")), D("1.75"))
        self.assertEqual(taker_fee(D(100), D("0.5"), D("0.5")), D("0.875"))
        self.assertEqual(taker_fee(D(0), D("0.5")), D(0))

    def test_per_contract_estimate_never_understates_a_larger_fill(self):
        for price in (D("0.05"), D("0.37"), D("0.5"), D("0.93")):
            for count in (1, 7, 78, 1000):
                with self.subTest(price=price, count=count):
                    self.assertGreaterEqual(fee_per_contract(price) * count, taker_fee(D(count), price))

    def test_rejects_impossible_inputs(self):
        for args in ((D(-1), D("0.5")), (D(1), D(0)), (D(1), D(1)), (D(1), D("0.5"), D(-1))):
            with self.subTest(args=args), self.assertRaises(ValueError):
                taker_fee(*args)


class ModelTests(unittest.TestCase):
    def test_proposal_validation(self):
        bad = [dict(side="Yes"), dict(estimated_yes_probability=1), dict(estimated_yes_probability=0),
               dict(requested_budget=0), dict(max_entry_price="1.2"), dict(proposal_id=""),
               dict(received_at=datetime(2026, 10, 10)), dict(expires_at=NOW), dict(requested_budget=True)]
        for overrides in bad:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                proposal(**overrides)

    def test_no_side_uses_complement_probability(self):
        self.assertEqual(proposal(side="no", estimated_yes_probability="0.35").win_probability, D("0.65"))

    def test_asks_derive_from_opposing_bids_with_their_depth(self):
        # Live KXNFLGAME-26OCT18BALCLE-CLE book on 2026-10-10; the live quote showed
        # YES ask 0.37 x 2702.70 and NO ask 0.64 x 1851.84.
        book = OrderBook("CLE", "BALCLE", NOW,
                         yes_bids=[PriceLevel(p, q) for p, q in (("0.33", "6060.60"), ("0.36", "1851.84"))],
                         no_bids=[PriceLevel(p, q) for p, q in (("0.62", "6140.26"), ("0.63", "2702.70"))])
        self.assertEqual(book.best_ask("yes"), PriceLevel(D("0.37"), D("2702.70")))
        self.assertEqual(book.best_ask("no"), PriceLevel(D("0.64"), D("1851.84")))
        self.assertEqual([lvl.price for lvl in book.asks("yes")], [D("0.37"), D("0.38")])

    def test_empty_side_has_no_ask(self):
        self.assertIsNone(OrderBook("M", "E", NOW, yes_bids=[PriceLevel("0.4", "10")]).best_ask("yes"))


class PolicyTests(unittest.TestCase):
    def test_defaults_match_brief(self):
        self.assertEqual(DEFAULT_POLICY.kelly_fraction, D("0.25"))
        self.assertEqual((DEFAULT_POLICY.market_cap, DEFAULT_POLICY.agent_cap,
                          DEFAULT_POLICY.event_group_cap, DEFAULT_POLICY.total_cap),
                         (D("0.05"), D("0.15"), D("0.10"), D("0.30")))
        self.assertEqual(DEFAULT_POLICY.to_dict()["marketCap"], "0.05")

    def test_rejects_out_of_range(self):
        for overrides in (dict(market_cap=0), dict(total_cap="1.5"), dict(max_quote_age_seconds=0),
                          dict(max_quote_age_seconds=True), dict(version=" "), dict(kelly_fraction=float("nan"))):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                Policy(**overrides)


class SizingTests(unittest.TestCase):
    def test_brief_worked_example(self):
        # $1,000 equity, 65% YES, $0.50 ask + $0.02 buffer, reliability 0.6 -> $40.625.
        self.assertEqual(full_kelly(D("0.65"), D("0.52")), D("0.13") / D("0.48"))
        stake = target_stake(equity=D(1000), win_probability=D("0.65"), effective_cost=D("0.52"),
                             reliability=D("0.6"), requested_budget=D(100), kelly_fraction=D("0.25"))
        self.assertEqual(stake, D("40.625"))

    def test_capped_by_request(self):
        self.assertEqual(target_stake(equity=D(1000), win_probability=D("0.65"), effective_cost=D("0.52"),
                                      reliability=D("0.6"), requested_budget=D(25), kelly_fraction=D("0.25")), D(25))

    def test_no_edge_or_invalid_cost_sizes_zero(self):
        for p, c in ((D("0.5"), D("0.5")), (D("0.4"), D("0.5")), (D("0.9"), D(1)), (D("0.9"), D(0))):
            with self.subTest(p=p, c=c):
                self.assertEqual(full_kelly(p, c), D(0))
        self.assertEqual(target_stake(equity=D(0), win_probability=D("0.9"), effective_cost=D("0.5"),
                                      reliability=D(1), requested_budget=D(10), kelly_fraction=D(1)), D(0))


class ReliabilityTests(unittest.TestCase):
    def test_new_agent_starts_at_prior(self):
        score = score_agent("a1", [])
        self.assertEqual((score.score, score.resolved_count, score.brier), (D("0.5"), 0, None))

    def test_one_good_forecast_moves_score_a_little(self):
        score = score_agent("a1", [Forecast("a1", "M1", "0.9", 1)])
        self.assertEqual(score.brier, D("0.01"))
        expected = D(20) / D(21) * D("0.5") + D(1) / D(21) * D("0.96")
        self.assertEqual(score.score, expected)

    def test_first_forecast_per_market_only_and_unresolved_ignored(self):
        forecasts = [Forecast("a1", "M1", "0.9", 1), Forecast("a1", "M1", "0.1", 1),
                     Forecast("a1", "M2", "0.7"), Forecast("a2", "M1", "0.2", 1)]
        scores = score_all(forecasts)
        self.assertEqual(scores["a1"].resolved_count, 1)
        self.assertEqual(scores["a1"].brier, D("0.01"))
        self.assertEqual(scores["a2"].brier, D("0.64"))

    def test_terrible_forecaster_bottoms_out_but_stays_bounded(self):
        forecasts = [Forecast("a1", f"M{i}", "0.99", 0) for i in range(200)]
        score = score_agent("a1", forecasts)
        self.assertGreaterEqual(score.score, D(0))
        self.assertLess(score.score, D("0.05"))


if __name__ == "__main__":
    unittest.main()
