"""The user's own trading rules: catalog, migration, the trading gate, and that checks and the governor use them."""
import copy
import datetime as dt
from decimal import Decimal as D
import unittest

import trading_rules
from agent_chat import AgentChat
from engine import main_seed, migrate, seed
from governor import rules as codes
from test_engine import FakeVault, FakeVenue
from test_governor_service import ServiceTest
from workspace import Workspace

MANDATE = {"autoLimit": 25, "dailyCap": 100, "minimum": 0, "newMarketApproval": False}


class CatalogTests(unittest.TestCase):
    def test_every_rule_is_explained_and_bounded(self):
        groups = {g["id"] for g in trading_rules.GROUPS}
        self.assertEqual(set(trading_rules.KEYS), set(trading_rules.LEGACY_VALUES))
        for rule in trading_rules.RULES:
            with self.subTest(rule=rule["key"]):
                self.assertIn(rule["group"], groups)
                self.assertTrue(rule["label"] and rule["explanation"] and rule["example"])
                self.assertLess(rule["min"], rule["max"])
                self.assertIn(rule["unit"], ("percent", "dollars", "seconds", "count"))
        self.assertTrue(all(c["label"] and c["explanation"] for c in trading_rules.ALWAYS_ON))
        self.assertNotIn("slippage", str(trading_rules.catalog()).lower())

    def test_validation_names_every_problem(self):
        self.assertEqual(trading_rules.validate(trading_rules.LEGACY_VALUES), trading_rules.LEGACY_VALUES)
        bad = {**trading_rules.LEGACY_VALUES, "kellyMultiplier": 0, "trustWeight": 2.5, "marketCapPercent": True,
               "dailySpendingCeiling": float("inf"), "surprise": 1}
        del bad["approvalWindowSeconds"]
        with self.assertRaises(ValueError) as caught:
            trading_rules.validate(bad)
        message = str(caught.exception)
        for part in ("Unknown rule surprise.", "Kelly multiplier must be between 1% and 100%.",
                     "How fast trust changes must be a whole number.", "Per-market cap must be a number.",
                     "Daily spending ceiling must be a number.", "Approval window is required."):
            self.assertIn(part, message)
        self.assertEqual(trading_rules.validate({**trading_rules.LEGACY_VALUES, "marketCapPercent": 0})["marketCapPercent"], 0)
        self.assertEqual(trading_rules.validate({**trading_rules.LEGACY_VALUES, "marketDollarLimit": 12.345})["marketDollarLimit"], 12.35)

    def test_the_policy_is_built_from_the_users_values(self):
        s = trading_rules.fixture(main_seed(), {"kellyMultiplier": 40, "totalCapPercent": 0, "quoteMaxAgeSeconds": 45})
        policy = trading_rules.to_policy(s)
        self.assertEqual((policy.version, policy.kelly_fraction, policy.total_cap, policy.max_quote_age_seconds),
                         ("user-rules-v1", D("0.4"), D("0"), 45))
        self.assertEqual((policy.reliability_prior, policy.reliability_prior_weight, policy.approval_ttl_seconds),
                         (D("0.5"), 20, 900))
        self.assertIsNone(trading_rules.to_policy(main_seed()))


class MigrationTests(unittest.TestCase):
    def test_a_new_workspace_has_no_rules_and_no_presets(self):
        s = main_seed()
        self.assertEqual(s["rules"], {"version": 0, "setAt": None, "values": None, "previous": None})
        self.assertNotIn("safety", s)
        self.assertEqual(s["permissions"], {"automation": True})

    def test_an_existing_workspace_must_set_rules_and_sees_its_old_values_as_hints(self):
        old = main_seed()
        del old["rules"]
        old["permissions"] = {"dailyCap": 80, "automation": True}
        old["safety"] = {"dailyLossCap": 25, "marketCap": 40, "exposureCap": 300, "slippage": 1}
        s = migrate(copy.deepcopy(old))
        self.assertIsNone(s["rules"]["values"])
        self.assertEqual({k: s["rules"]["previous"][k] for k in ("dailySpendingCeiling", "marketDollarLimit",
                                                                 "exposureDollarLimit", "dailyLossDollarLimit")},
                         {"dailySpendingCeiling": 80, "marketDollarLimit": 40, "exposureDollarLimit": 300,
                          "dailyLossDollarLimit": 25})
        self.assertEqual(s["rules"]["previous"]["kellyMultiplier"], 25)
        self.assertNotIn("safety", s)
        self.assertEqual(s["permissions"], {"automation": True})
        self.assertEqual(migrate(copy.deepcopy(s)), s)  # running it again changes nothing

    def test_the_presenter_demo_keeps_its_scripted_limits(self):
        demo = seed()
        self.assertEqual(demo["safety"]["marketCap"], 50)
        self.assertNotIn("rules", migrate(demo))


class GateTests(unittest.TestCase):
    """A workspace without rules cannot trade or set mandates; setting rules is versioned and logged."""

    def setUp(self):
        self.s = main_seed()
        self.v = FakeVenue()
        self.w = Workspace(FakeVault())
        self.w.venue = lambda account: self.v
        self.a = {"id": "account", "name": "Kalshi", "venue": "kalshi", "environment": "sandbox", "status": "connected",
                  "credentialRef": "REF", "ceiling": {}}
        self.g = {"id": "agent", "name": "Agent", "accountId": "account", "status": "active", "personality": None,
                  "mandate": dict(MANDATE), "modelValidated": True}
        self.s["accounts"], self.s["agentConfigs"] = [self.a], [self.g]

    def propose(self, amount=10, key="one"):
        return self.w.proposal(self.s, {"agentId": "agent", "marketId": "BTC", "direction": "Yes", "amount": amount,
                                        "reasoning": "r", "idempotencyKey": key})

    def test_without_rules_every_trade_is_blocked(self):
        order = self.propose()
        self.assertEqual((order["decision"], self.v.calls), ("BLOCK", 0))
        gate = next(c for c in order["evaluations"][-1]["checks"] if c["name"] == "Trading rules set")
        self.assertEqual(gate["outcome"], "BLOCK")

    def test_without_rules_mandates_and_resuming_are_refused(self):
        with self.assertRaisesRegex(ValueError, "Set your trading rules before an agent mandate"):
            self.w.action(self.s, {"action": "mandate-save", "id": "agent", "mandate": dict(MANDATE)})
        self.g["status"] = "paused"
        with self.assertRaisesRegex(ValueError, "Set your trading rules before resuming"):
            self.w.action(self.s, {"action": "agent-status", "id": "agent", "status": "active"})

    def test_setting_rules_is_versioned_logged_and_enables_trading(self):
        result = self.w.action(self.s, {"action": "rules-save", "values": dict(trading_rules.LEGACY_VALUES)})
        self.assertEqual((result["version"], self.s["events"][0]["text"]), (1, "Trading rules set. Agents can trade within them."))
        self.assertEqual(self.propose()["decision"], "ALLOW")
        changed = {**trading_rules.LEGACY_VALUES, "kellyMultiplier": 10, "marketDollarLimit": 20}
        self.assertEqual(self.w.action(self.s, {"action": "rules-save", "values": changed})["version"], 2)
        self.assertEqual(self.s["events"][0]["text"], "Trading rules updated (version 2): Kelly multiplier, Per-market dollar limit.")
        with self.assertRaisesRegex(ValueError, "Kelly multiplier is required"):
            self.w.action(self.s, {"action": "rules-save", "values": {}})
        self.assertEqual(self.s["rules"]["version"], 2)  # a refused save changes nothing

    def test_mandates_have_no_presets(self):
        trading_rules.fixture(self.s)
        self.w.action(self.s, {"action": "mandate-save", "id": "agent", "personality": "Aggressive", "mandate": dict(MANDATE)})
        self.assertEqual((self.g["personality"], self.g["mandate"]), (None, MANDATE))
        public = self.w.public(self.s)
        self.assertNotIn("personalities", public)
        self.assertEqual([r["key"] for r in public["rulesCatalog"]["rules"]], trading_rules.KEYS)
        with self.assertRaisesRegex(ValueError, "four mandate restrictions"):
            self.w.action(self.s, {"action": "mandate-save", "id": "agent", "mandate": {"autoLimit": 1}})

    def test_the_daily_ceiling_is_a_rule_not_a_permission(self):
        trading_rules.fixture(self.s)
        with self.assertRaisesRegex(ValueError, "under Trading Rules"):
            self.w.action(self.s, {"action": "permissions", "values": {"dailyCap": 5}})
        self.w.action(self.s, {"action": "permissions", "values": {"automation": False}})
        self.assertEqual((self.s["permissions"]["automation"], self.g["status"]), (False, "paused"))

    def test_order_checks_use_the_users_limits(self):
        trading_rules.fixture(self.s, {"marketDollarLimit": 5})
        order = self.propose(amount=10)
        budget = next(c for c in order["evaluations"][-1]["checks"] if c["name"] == "Cumulative market budget")
        self.assertEqual((order["decision"], budget["outcome"], budget["reason"]),
                         ("BLOCK", "BLOCK", "Your per-market dollar limit is $5.00."))
        trading_rules.fixture(self.s, {"dailySpendingCeiling": 8})
        self.assertEqual(self.propose(amount=10, key="two")["decision"], "BLOCK")
        names = {c["name"] for c in self.propose(amount=5, key="three")["evaluations"][-1]["checks"]}
        self.assertNotIn("Slippage", names)

    def test_price_freshness_is_the_users_rule(self):
        trading_rules.fixture(self.s, {"quoteMaxAgeSeconds": 5})
        stale = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=20)).isoformat()
        quote = self.v.get_quote
        self.v.get_quote = lambda k, d: {**quote(k, d), "quoteAt": stale}
        order = self.propose()
        fresh = next(c for c in order["evaluations"][-1]["checks"] if c["name"] == "Fresh quote")
        self.assertEqual((fresh["outcome"], fresh["reason"]), ("BLOCK", "Prices older than 5 seconds cannot execute."))

    def test_the_chat_agent_cannot_propose_without_rules(self):
        chat = AgentChat(self.w, None)
        self.assertEqual(chat._trading_blocked(self.s, self.g, self.a), "the account owner has not set their trading rules yet")
        trading_rules.fixture(self.s)
        self.assertIsNone(chat._trading_blocked(self.s, self.g, self.a))


class GovernorRuleTests(ServiceTest):
    """The governor sizes, caps and versions every decision with the workspace's own rules."""

    def decide(self, **overrides):
        trading_rules.fixture(self.s, overrides)
        code, body = self.submit(proposalId=f"p-{len(self.s.get('governorRequests', []))}")
        self.assertEqual(code, 202)
        self.service.run_batch(self.s)
        return next(d for d in self.s["governorDecisions"] if d["proposalId"] == body["governorProposalId"])

    def test_no_rules_no_proposals(self):
        self.s["rules"] = trading_rules.unset()
        code, body = self.submit()
        self.assertEqual(code, 400)
        self.assertIn("no trading rules yet", body["errors"][0]["error"])
        self.assertIn("no trading rules yet", self.service.schema(self.s)["notes"][-1])

    def test_queued_proposals_are_rejected_if_rules_disappear(self):
        trading_rules.fixture(self.s)
        self.submit()
        self.s["rules"] = trading_rules.unset()
        self.service.run_batch(self.s)
        self.assertEqual((self.s["governorRequests"][0]["status"], self.s["governorRequests"][0]["note"]),
                         ("rejected", "Trading rules are not set for this workspace."))

    def test_the_kelly_multiplier_sets_the_stake(self):
        wide = dict(marketCapPercent=100, agentCapPercent=100, eventCapPercent=100, totalCapPercent=100)
        small = self.decide(kellyMultiplier=5, **wide)
        self.assertEqual(small["policyVersion"], f"user-rules-v{self.s['rules']['version']}")
        self.s["orders"].clear()  # so the second decision is not blocked by the first position
        self.venue.positions = []
        large = self.decide(kellyMultiplier=50, **wide)
        self.assertEqual(large["policyVersion"], f"user-rules-v{self.s['rules']['version']}")  # the newer rules
        self.assertGreater(D(large["approvedBudgetUsd"]), D(small["approvedBudgetUsd"]))

    def test_a_zero_percent_cap_stops_new_risk(self):
        decision = self.decide(marketCapPercent=0)
        self.assertEqual(decision["decision"], "rejected")
        self.assertIn(codes.MARKET_CAP, decision["reasonCodes"])

    def test_the_approval_window_is_the_users_rule(self):
        trading_rules.fixture(self.s, {"approvalWindowSeconds": 120})
        self.s["agentConfigs"][0]["mandate"]["autoLimit"] = 1  # so the governed order waits for approval
        self.submit()
        before = dt.datetime.now(dt.timezone.utc)
        self.service.run_batch(self.s)
        order = next(o for o in self.s["orders"] if o.get("governedTerms"))
        approve_by = dt.datetime.fromisoformat(order["governedTerms"]["approveBy"])
        self.assertAlmostEqual((approve_by - before).total_seconds(), 120, delta=5)


if __name__ == "__main__":
    unittest.main()
