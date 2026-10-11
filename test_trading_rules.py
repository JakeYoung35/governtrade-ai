"""The user's trading rules (schema 2): percentages of the account with starting positions, the conversion of
schema 1 dollar rules, the trading gate, and that order checks and the governor use the user's values."""
import copy
import datetime as dt
from decimal import Decimal as D
import unittest

import trading_rules
from agent_chat import AgentChat
from engine import main_seed, migrate, seed
from governor import rules as codes
from test_engine import MANDATE, FakeVault, FakeVenue
from test_governor_service import ServiceTest
from workspace import Workspace

# A schema 1 workspace as PR #36 saved it: dollar rules, a dollar mandate and a copied account ceiling.
V1_VALUES = {"kellyMultiplier": 20, "marketCapPercent": 5, "agentCapPercent": 15, "eventCapPercent": 10,
             "totalCapPercent": 30, "dailySpendingCeiling": 100, "marketDollarLimit": 25, "exposureDollarLimit": 500,
             "dailyLossDollarLimit": 50, "lossBreakerPercent": 3, "quoteMaxAgeSeconds": 45,
             "proposalLifetimeSeconds": 60, "approvalWindowSeconds": 900, "startingTrustPercent": 50, "trustWeight": 20}


def v1_workspace(balance=500.0):
    s = main_seed()
    s["rules"] = {"version": 3, "setAt": "2026-10-10T00:00:00+00:00", "values": dict(V1_VALUES), "previous": None}
    account = {"id": "account", "name": "Kalshi", "venue": "kalshi", "environment": "sandbox", "status": "connected",
               "credentialRef": "REF", "ceiling": {"dailyCap": 100}, "baseline": {"day": "2026-10-10", "equity": 500.0}}
    if balance is not None:
        account["balance"] = {"cash": balance, "reserved": 0, "positionValue": 0}
    s["accounts"] = [account]
    s["agentConfigs"] = [{"id": "agent", "name": "Agent", "accountId": "account", "status": "active",
                          "mandate": {"autoLimit": 10, "dailyCap": 50, "minimum": 100, "newMarketApproval": True}}]
    return s


class CatalogTests(unittest.TestCase):
    def test_every_rule_is_a_bounded_explained_slider_with_a_start(self):
        groups = {g["id"] for g in trading_rules.GROUPS}
        self.assertEqual(len(trading_rules.RULES), 12)
        for rule in trading_rules.RULES:
            with self.subTest(rule=rule["key"]):
                self.assertIn(rule["group"], groups)
                self.assertTrue(rule["label"] and rule["explanation"])
                self.assertTrue(rule["min"] <= rule["start"] <= rule["max"])
                self.assertGreater(rule["step"], 0)
                self.assertIn(rule.get("scale", "linear"), ("linear", "log"))
                self.assertIn(rule["unit"], ("percent", "seconds", "count"))
        self.assertNotIn("dollars", {r["unit"] for r in trading_rules.RULES})  # every money rule is a percentage
        self.assertEqual(trading_rules.validate(trading_rules.DEFAULTS), trading_rules.DEFAULTS)
        mandate = dict(trading_rules.MANDATE_DEFAULTS)
        self.assertEqual(trading_rules.validate_mandate(mandate, trading_rules.DEFAULTS["dailySpendingPercent"]), mandate)

    def test_the_approved_starting_positions(self):
        self.assertEqual(trading_rules.DEFAULTS, {
            "kellyMultiplier": 25, "marketCapPercent": 5, "agentCapPercent": 15, "eventCapPercent": 10,
            "totalCapPercent": 30, "dailySpendingPercent": 20, "dailyLossPercent": 3, "quoteMaxAgeSeconds": 30,
            "proposalLifetimeSeconds": 60, "approvalWindowSeconds": 900, "startingTrustPercent": 50, "trustWeight": 20})
        self.assertEqual(trading_rules.MANDATE_DEFAULTS, {"autoLimitPercent": 2, "dailyCapPercent": 10,
                                                          "minimumPercent": 20, "newMarketApproval": False})

    def test_validation_names_every_problem(self):
        bad = {**trading_rules.DEFAULTS, "kellyMultiplier": 0, "trustWeight": 2.5, "marketCapPercent": True,
               "dailySpendingPercent": float("inf"), "surprise": 1}
        del bad["approvalWindowSeconds"]
        with self.assertRaises(ValueError) as caught:
            trading_rules.validate(bad)
        for part in ("Unknown rule surprise.", "Kelly multiplier must be between 1% and 100%.",
                     "How fast trust changes must be a whole number.", "Per-market cap must be a number.",
                     "Daily spending ceiling must be a number.", "Approval window is required."):
            self.assertIn(part, str(caught.exception))
        self.assertEqual(trading_rules.validate({**trading_rules.DEFAULTS, "marketCapPercent": 0})["marketCapPercent"], 0)

    def test_mandate_validation(self):
        ok = dict(trading_rules.MANDATE_DEFAULTS)
        for raw, error in [({**ok, "dailyCapPercent": 25}, "cannot exceed your daily spending ceiling of 20%"),
                           ({**ok, "autoLimitPercent": 12}, "automatic trade limit cannot exceed"),
                           ({**ok, "minimumPercent": 101}, "Minimum cash reserve must be between"),
                           ({**ok, "newMarketApproval": "yes"}, "Choose whether new markets"),
                           ({"autoLimit": 25}, "four mandate restrictions")]:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                trading_rules.validate_mandate(raw, 20)

    def test_the_policy_is_built_from_the_users_values(self):
        s = trading_rules.fixture(main_seed(), {"kellyMultiplier": 40, "totalCapPercent": 0, "dailyLossPercent": 2.5})
        policy = trading_rules.to_policy(s)
        self.assertEqual((policy.version, policy.kelly_fraction, policy.total_cap, policy.daily_loss_limit),
                         ("user-rules-v1", D("0.4"), D("0"), D("0.025")))
        self.assertIsNone(trading_rules.to_policy(main_seed()))

    def test_account_value_and_dollars(self):
        self.assertEqual(trading_rules.account_value({"balance": {"cash": 400, "reserved": 50, "positionValue": 50}}), 500)
        self.assertEqual(trading_rules.account_value({"cash": D("12.5")}), 12.5)
        self.assertIsNone(trading_rules.account_value({"status": "connected"}))
        self.assertIsNone(trading_rules.account_value({"balance": {"cash": 0}}))
        self.assertEqual((trading_rules.dollars(5, 500), trading_rules.dollars(5, None)), (25, 0))


class MigrationTests(unittest.TestCase):
    def test_a_new_workspace_must_review_the_starting_positions(self):
        s = main_seed()
        self.assertEqual(s["rules"], trading_rules.unset())
        self.assertFalse(trading_rules.ready(s))

    def test_schema_1_dollar_rules_are_converted_for_review(self):
        s = migrate(v1_workspace(balance=500.0))
        rules = s["rules"]
        self.assertEqual((rules["schema"], rules["version"], rules["values"], rules["converted"]), (2, 3, None, True))
        self.assertFalse(trading_rules.ready(s))  # nothing trades until the user reviews and saves
        p = rules["previous"]
        self.assertEqual(p["dailySpendingPercent"], 20)  # $100 of $500
        self.assertEqual(p["marketCapPercent"], 5)  # stricter of 5% and $25 of $500 (5%)
        self.assertEqual(p["totalCapPercent"], 30)  # stricter of 30% and $500 of $500 (100%)
        self.assertEqual(p["dailyLossPercent"], 3)  # stricter of 3% and $50 of the $500 day open (10%)
        self.assertEqual((p["kellyMultiplier"], p["quoteMaxAgeSeconds"]), (20, 45))  # unchanged rules carry over
        self.assertEqual(trading_rules.validate(p), p)

    def test_dollar_mandates_and_ceilings_become_shares_of_the_account(self):
        s = migrate(v1_workspace(balance=500.0))
        self.assertEqual(s["agentConfigs"][0]["mandate"], {"autoLimitPercent": 2, "dailyCapPercent": 10,
                                                           "minimumPercent": 20, "newMarketApproval": True})
        self.assertEqual(s["accounts"][0]["ceiling"], {})  # $100 was the copied workspace ceiling, not a choice
        lower = v1_workspace(balance=500.0)
        lower["accounts"][0]["ceiling"] = {"dailyCap": 40}
        self.assertEqual(migrate(lower)["accounts"][0]["ceiling"], {"dailyPercent": 8})  # $40 of $500

    def test_without_a_balance_dollar_rules_fall_back_and_mandates_are_cleared(self):
        s = migrate(v1_workspace(balance=None))
        self.assertEqual(s["rules"]["previous"]["dailySpendingPercent"], trading_rules.DEFAULTS["dailySpendingPercent"])
        self.assertIsNone(s["agentConfigs"][0]["mandate"])

    def test_a_pre_rules_workspace_reviews_the_starting_positions(self):
        old = main_seed()
        del old["rules"]
        old["permissions"] = {"dailyCap": 80, "automation": True}
        old["safety"] = {"dailyLossCap": 25, "marketCap": 40, "exposureCap": 300, "slippage": 1}
        s = migrate(copy.deepcopy(old))
        self.assertEqual(s["rules"], trading_rules.unset())
        self.assertNotIn("safety", s)
        self.assertEqual(s["permissions"], {"automation": True})
        self.assertEqual(migrate(copy.deepcopy(s)), s)  # running it again changes nothing

    def test_postgres_decimals_convert(self):
        s = v1_workspace(balance=None)
        s["accounts"][0]["balance"] = {"cash": D("400.00"), "reserved": D("0"), "positionValue": D("100.00")}
        s["agentConfigs"][0]["mandate"] = {"autoLimit": D("10.00"), "dailyCap": D("50.00"), "minimum": D("0"),
                                           "newMarketApproval": False}
        mandate = migrate(s)["agentConfigs"][0]["mandate"]
        self.assertEqual((mandate["autoLimitPercent"], type(mandate["autoLimitPercent"])), (2, int))

    def test_the_presenter_demo_keeps_its_scripted_limits(self):
        demo = seed()
        self.assertEqual(demo["safety"]["marketCap"], 50)
        self.assertNotIn("rules", migrate(demo))


class GateTests(unittest.TestCase):
    """Order checks turn the user's percentages into dollars of the account's current value."""

    def setUp(self):
        self.s = main_seed()
        self.v = FakeVenue()  # a $1,000 account
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

    def check(self, order, name):
        return next(c for c in order["evaluations"][-1]["checks"] if c["name"] == name)

    def test_without_rules_every_trade_is_blocked(self):
        order = self.propose()
        self.assertEqual((order["decision"], self.v.calls), ("BLOCK", 0))
        self.assertEqual(self.check(order, "Trading rules set")["outcome"], "BLOCK")

    def test_without_rules_mandates_and_resuming_are_refused(self):
        with self.assertRaisesRegex(ValueError, "Set your trading rules before an agent mandate"):
            self.w.action(self.s, {"action": "mandate-save", "id": "agent", "mandate": dict(MANDATE)})
        self.g["status"] = "paused"
        with self.assertRaisesRegex(ValueError, "Set your trading rules before resuming"):
            self.w.action(self.s, {"action": "agent-status", "id": "agent", "status": "active"})

    def test_saving_rules_is_versioned_logged_and_enables_trading(self):
        result = self.w.action(self.s, {"action": "rules-save", "values": dict(trading_rules.DEFAULTS)})
        self.assertEqual((result["version"], self.s["events"][0]["text"]), (1, "Trading rules set. Agents can trade within them."))
        self.assertEqual(self.propose()["decision"], "ALLOW")
        changed = {**trading_rules.DEFAULTS, "kellyMultiplier": 10, "marketCapPercent": 2}
        self.assertEqual(self.w.action(self.s, {"action": "rules-save", "values": changed})["version"], 2)
        self.assertEqual(self.s["events"][0]["text"], "Trading rules updated (version 2): Kelly multiplier, Per-market cap.")
        events = len(self.s["events"])
        self.assertEqual(self.w.action(self.s, {"action": "rules-save", "values": changed})["version"], 2)  # unchanged
        self.assertEqual(len(self.s["events"]), events)

    def test_mandates_are_percentages_within_the_ceiling(self):
        trading_rules.fixture(self.s)
        self.w.action(self.s, {"action": "mandate-save", "id": "agent", "mandate": dict(trading_rules.MANDATE_DEFAULTS)})
        self.assertEqual(self.g["mandate"], trading_rules.MANDATE_DEFAULTS)
        self.a["ceiling"] = {"dailyPercent": 5}  # this account's own, lower ceiling
        with self.assertRaisesRegex(ValueError, "daily spending ceiling of 5%"):
            self.w.action(self.s, {"action": "mandate-save", "id": "agent", "mandate": dict(trading_rules.MANDATE_DEFAULTS)})
        public = self.w.public(self.s)
        self.assertEqual([f["key"] for f in public["rulesCatalog"]["mandateFields"]], trading_rules.MANDATE_KEYS)

    def test_the_account_ceiling_is_a_percentage(self):
        trading_rules.fixture(self.s)
        self.w.action(self.s, {"action": "permissions", "scope": "account", "values": {"dailyPercent": 7.5}})
        self.assertEqual(self.a["ceiling"], {"dailyPercent": 7.5})
        self.w.refresh(self.s, self.a)
        self.assertEqual(self.w.public(self.s)["accounts"][0]["effectiveCeiling"]["dailyCap"], 75)  # 7.5% of $1,000
        for bad in ({"dailyPercent": 101}, {"dailyCap": 5}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.w.action(self.s, {"action": "permissions", "scope": "account", "values": bad})
        self.w.action(self.s, {"action": "permissions", "scope": "account", "values": {"dailyPercent": None}})
        self.assertEqual(self.a["ceiling"], {})
        with self.assertRaisesRegex(ValueError, "under Trading Rules"):
            self.w.action(self.s, {"action": "permissions", "values": {"dailyCap": 5}})

    def test_order_checks_turn_percentages_into_dollars_of_the_account(self):
        trading_rules.fixture(self.s, {"marketCapPercent": 0.5})  # $5 of $1,000
        order = self.propose(amount=10)
        budget = self.check(order, "Cumulative market budget")
        self.assertEqual((order["decision"], budget["reason"]), ("BLOCK", "Your per-market cap is $5.00 of this account."))
        trading_rules.fixture(self.s, {"dailySpendingPercent": 0.8})  # $8 of $1,000
        self.assertEqual(self.propose(amount=10, key="two")["decision"], "BLOCK")
        self.v.cash = 2000  # the same 0.8% of a $2,000 account is $16
        self.assertEqual(self.propose(amount=10, key="three")["decision"], "ALLOW")

    def test_the_mandate_scales_with_the_account(self):
        trading_rules.fixture(self.s)
        self.assertEqual(self.check(self.propose(amount=25), "Automatic authorization")["outcome"], "ALLOW")  # 2.5% of $1,000
        self.v.cash = 500  # 2.5% is now $12.50
        auto = self.check(self.propose(amount=25, key="two"), "Automatic authorization")
        self.assertEqual((auto["outcome"], auto["reason"]), ("REQUIRE_APPROVAL", "Above $12.50 requires approval."))

    def test_trading_stops_when_the_daily_loss_reaches_the_limit(self):
        trading_rules.fixture(self.s, {"dailyLossPercent": 5})  # $50 of the $1,000 day open
        self.w.refresh(self.s, self.a)
        self.v.cash = 951
        self.assertEqual(self.check(self.propose(amount=5, key="down-49"), "Daily loss")["outcome"], "ALLOW")
        self.v.cash = 950
        self.assertEqual(self.check(self.propose(amount=5, key="down-50"), "Daily loss")["outcome"], "BLOCK")

    def test_a_breaker_reset_also_resumes_the_order_check(self):
        trading_rules.fixture(self.s, {"dailyLossPercent": 5})
        self.w.refresh(self.s, self.a)  # the day opens at $1,000
        self.v.cash = 900  # down 10%, past the 5% limit
        self.assertEqual(self.check(self.propose(amount=5, key="down-10"), "Daily loss")["outcome"], "BLOCK")
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        self.s.setdefault("governor", {}).setdefault("breakers", {})["account"] = {  # the operator resets at $900
            "latched": False, "rebaseDay": today, "rebaseEquity": "900"}
        self.assertEqual(self.check(self.propose(amount=5, key="after-reset"), "Daily loss")["outcome"], "ALLOW")
        self.v.cash = 855  # another 5% from the reset value
        self.assertEqual(self.check(self.propose(amount=5, key="down-again"), "Daily loss")["outcome"], "BLOCK")

    def test_price_freshness_is_the_users_rule(self):
        trading_rules.fixture(self.s, {"quoteMaxAgeSeconds": 5})
        stale = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=20)).isoformat()
        quote = self.v.get_quote
        self.v.get_quote = lambda k, d: {**quote(k, d), "quoteAt": stale}
        fresh = self.check(self.propose(), "Fresh quote")
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

    def test_the_kelly_multiplier_sets_the_stake(self):
        wide = dict(marketCapPercent=100, agentCapPercent=100, eventCapPercent=100, totalCapPercent=100)
        small = self.decide(kellyMultiplier=5, **wide)
        self.assertEqual(small["policyVersion"], f"user-rules-v{self.s['rules']['version']}")
        self.s["orders"].clear()
        self.venue.positions = []
        large = self.decide(kellyMultiplier=50, **wide)
        self.assertGreater(D(large["approvedBudgetUsd"]), D(small["approvedBudgetUsd"]))

    def test_a_zero_percent_cap_stops_new_risk(self):
        decision = self.decide(marketCapPercent=0)
        self.assertEqual(decision["decision"], "rejected")
        self.assertIn(codes.MARKET_CAP, decision["reasonCodes"])


if __name__ == "__main__":
    unittest.main()
