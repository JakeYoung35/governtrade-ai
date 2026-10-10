"""Step 11: the offline replay demo runs with no network and every beat shows its intended outcome."""
from decimal import Decimal as D
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

import replay_demo  # noqa: E402
from governor import rules  # noqa: E402


class ReplayDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def no_network(*args, **kwargs):
            raise AssertionError("The replay demo must not use the network.")
        cls.lines = []
        with patch("urllib.request.urlopen", no_network), patch("adapters.urlopen", no_network):
            cls.results = replay_demo.run_demo(say=cls.lines.append)

    def test_beat1_approve_conflict_and_resize_in_one_batch(self):
        views = {v["agentId"]: v for v in self.results["beat1"].values()}
        self.assertEqual((views["reference"]["decision"]["decision"], views["reference"]["order"]["status"]), ("approved", "filled"))
        self.assertEqual(views["momentum"]["decision"]["reasonCodes"], [rules.CONFLICT])
        self.assertEqual(views["aggressive"]["decision"]["decision"], "resized")
        self.assertIn(rules.MARKET_CAP, views["aggressive"]["decision"]["reasonCodes"])
        self.assertLessEqual(D(views["aggressive"]["decision"]["approvedBudgetUsd"]), D(50))

    def test_beat2_identical_retry_does_not_trade_twice(self):
        self.assertEqual(self.results["beat2"], {"code": 200, "sameProposal": True, "newOrders": 0})

    def test_beat3_price_crash_latches_the_breaker(self):
        self.assertTrue(self.results["beat3"]["latched"])
        self.assertEqual(self.results["beat3"]["view"]["decision"]["reasonCodes"], [rules.LOSS_BREAKER])
        self.assertIn("below today's base", self.results["beat3"]["reason"])

    def test_beat4_reset_resumes_trading(self):
        self.assertFalse(self.results["beat4"]["latched"])
        view = self.results["beat4"]["view"]
        self.assertEqual((view["status"], view["order"]["status"]), ("submitted", "filled"))

    def test_beat5_settlement_moves_reliability_and_sizing(self):
        beat = self.results["beat5"]
        self.assertEqual(beat["scored"], 3)
        self.assertLess(beat["reliability"]["reference"], D("0.5"))
        self.assertGreater(beat["reliability"]["aggressive"], D("0.5"))
        self.assertGreater(beat["targets"]["aggressive"], beat["targets"]["reference"])

    def test_every_batch_replays_and_output_is_labeled_simulated(self):
        self.assertTrue(self.results["replays"])
        self.assertIn("paper ledger", self.lines[0])
        self.assertTrue(any("Replay check: all batches reproduce" in line for line in self.lines))


if __name__ == "__main__":
    unittest.main()
