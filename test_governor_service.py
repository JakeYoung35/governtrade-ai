"""Steps 8-9: the governed proposal API, batches through Workspace, the breaker and the server routes."""
import copy
from datetime import timedelta
from decimal import Decimal as D
import http.client
import itertools
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from adapters import VenueError
from engine import PERSONALITIES, main_seed
from governor import api, records, rules
from governor_service import GovernorService, utc_now
from test_engine import FakeVault, FakeVenue
from workspace import Workspace

MARKET, OTHER = "KXNFLGAME-26OCT18BALCLE-CLE", "KXNFLGAME-26OCT18BALCLE-BAL"


class GovernedVenue(FakeVenue):
    """FakeVenue plus order books; quotes follow the book's best ask unless overridden."""

    def __init__(self):
        super().__init__()
        self.books = {MARKET: ("0.50", "0.52", "1000"), OTHER: ("0.47", "0.55", "1000")}  # yes ask, no ask, depth
        self.quote_override = None
        self.positions = []
        self.outcomes = {}  # market_id -> YES settlement value once settled
        self.broken = set()

    def get_orderbook(self, market_id):
        if market_id not in self.books:
            raise VenueError("kalshi returned HTTP 404.")
        yes_ask, no_ask, depth = self.books[market_id]
        return {"id": market_id, "name": market_id, "eventTicker": "KXNFLGAME-26OCT18BALCLE", "agent": "sports",
                "venue": "kalshi", "open": True, "executionReady": True, "feeMultiplier": 1.0, "closeAt": None,
                "yesBids": [[str(D(1) - D(no_ask)), depth]], "noBids": [[str(D(1) - D(yes_ask)), depth]],
                "asOf": utc_now().isoformat()}

    def get_quote(self, k, d):
        quote = super().get_quote(k, d)
        yes_ask, no_ask, _ = self.books.get(k, ("0.50", "0.50", "0"))
        quote["price"] = self.quote_override or float(yes_ask if d == "Yes" else no_ask)
        return quote

    def get_market_outcome(self, market_id):
        if market_id in self.broken:
            raise VenueError("kalshi request failed.")
        outcome = self.outcomes.get(market_id)
        return {"id": market_id, "status": "finalized" if outcome is not None else "active", "outcome": outcome}

    def get_balance_positions(self):
        balance = super().get_balance_positions()
        balance["positions"] = copy.deepcopy(self.positions)
        return balance


def workspace_state():
    s = main_seed()
    s["accounts"] = [{"id": "account", "name": "Kalshi Demo", "venue": "kalshi", "environment": "sandbox",
                      "status": "connected", "credentialRef": "REF", "ceiling": {}}]
    s["agentConfigs"] = [{"id": agent, "name": agent, "accountId": "account", "status": "active", "personality": "Moderate",
                          "mandate": copy.deepcopy(PERSONALITIES["Moderate"]), "modelValidated": True}
                         for agent in ("agent", "agent-2")]
    return s


def submission(**overrides):
    payload = {"schemaVersion": 1, "proposalId": "p-1", "agentId": "agent", "marketId": MARKET, "side": "yes",
               "estimatedYesProbability": 0.65, "maxBudgetUsd": 20, "reasoning": "Edge over the ask."}
    payload.update(overrides)
    return {k: v for k, v in payload.items() if v is not None}


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.s = workspace_state()
        self.venue = GovernedVenue()
        self.workspace = Workspace(FakeVault())
        self.workspace.venue = lambda account: self.venue
        ids = itertools.count(1)
        self.service = GovernorService(self.workspace, new_id=lambda: f"id-{next(ids):03d}")

    def submit(self, **overrides):
        return self.service.submit(self.s, submission(**overrides))

    def request(self, gid):
        return next(r for r in self.s["governorRequests"] if r["governorProposalId"] == gid)


class ContractTests(ServiceTest):
    def test_schema_describes_itself_and_its_example_validates(self):
        described = self.service.schema()
        self.assertEqual(described["jsonSchema"]["required"], api.SCHEMA["required"])
        example = {**described["example"], "agentId": "agent"}
        normalized, errors = api.parse_submission(example, received_at=utc_now())
        self.assertEqual(errors, [])
        self.assertEqual(normalized["estimatedYesProbability"], "0.45")
        json.dumps(described)

    def test_field_level_errors_a_model_can_act_on(self):
        cases = {"surprise": dict(surprise=1), "estimatedYesProbability": dict(estimatedYesProbability="0.6"),
                 "maxBudgetUsd": dict(maxBudgetUsd=True), "side": dict(side="YES"), "schemaVersion": dict(schemaVersion=2),
                 "reasoning": dict(reasoning=None), "maxEntryPrice": dict(maxEntryPrice=1.5),
                 "expiresAt": dict(expiresAt=(utc_now() + timedelta(hours=2)).isoformat()),
                 "proposalId": dict(proposalId="has spaces")}
        for field, overrides in cases.items():
            code, body = self.submit(**overrides)
            with self.subTest(field=field):
                self.assertEqual(code, 400)
                self.assertIn(field, [e["field"] for e in body["errors"]])
                self.assertEqual(body["schema"], "/api/governor/schema")
        code, body = self.submit(expiresAt="2026-10-10T15:00:00")
        self.assertEqual((code, body["errors"][0]["field"]), (400, "expiresAt"))
        self.assertEqual(self.s["governorRequests"], [])

    def test_agent_must_exist_and_be_active(self):
        self.assertEqual(self.submit(agentId="nobody")[1]["errors"], [{"field": "agentId", "error": "is not a configured agent"}])
        self.s["agentConfigs"][0]["status"] = "paused"
        self.assertEqual(self.submit()[0], 400)

    def test_queue_idempotent_retry_and_conflict(self):
        code, body = self.submit()
        self.assertEqual((code, body["status"], body["proposalId"]), (202, "queued", "p-1"))
        self.assertEqual(self.s["governorForecasts"][0]["proposalId"], body["governorProposalId"])
        again_code, again = self.submit()
        self.assertEqual((again_code, again["governorProposalId"], again["status"]), (200, body["governorProposalId"], "queued"))
        self.assertEqual(len(self.s["governorRequests"]), 1)
        self.assertEqual(self.submit(maxBudgetUsd=21)[0], 409)
        self.assertEqual(self.submit(agentId="agent-2")[0], 202)  # proposal ids are scoped per agent


class BatchTests(ServiceTest):
    def run_one(self, **overrides):
        code, body = self.submit(**overrides)
        self.assertEqual(code, 202)
        self.service.run_batch(self.s)
        return self.service.lookup(self.s, body["governorProposalId"])

    def test_approved_stake_executes_through_the_workspace(self):
        view = self.run_one()
        self.assertEqual((view["status"], view["decision"]["decision"]), ("submitted", "approved"))
        self.assertEqual(self.venue.calls, 1)
        order = next(o for o in self.s["orders"] if o["id"] == view["order"]["id"])
        self.assertEqual((order["governorProposalId"], order["governorEventGroup"]), (view["governorProposalId"], "KXNFLGAME-26OCT18BALCLE"))
        # Authorization rounds up to whole cents; the governed order itself costs at most the approval.
        from governor.fees import taker_fee
        approved = D(view["decision"]["approvedBudgetUsd"])
        self.assertLess(D(str(order["amount"])) - approved, D("0.01"))
        contracts, limit = D(order["governedTerms"]["contracts"]), D(order["governedTerms"]["limitPrice"])
        self.assertEqual(contracts, D(view["decision"]["contracts"]))  # exactly what the governor approved
        self.assertLessEqual(limit * contracts + taker_fee(contracts, limit), approved)
        self.assertEqual(len(self.s["governorBatches"]), 1)
        self.assertEqual(records.replay(self.s["governorBatches"][0])[0].to_dict()["decision"], "approved")

    def test_user_mandate_still_applies_after_sizing(self):
        view = self.run_one(maxBudgetUsd=100, estimatedYesProbability=0.8)  # sized above the $25 automatic limit
        self.assertNotEqual(view["decision"]["decision"], "rejected")
        self.assertEqual((view["status"], view["order"]["status"]), ("submitted", "awaiting approval"))
        self.assertEqual(self.venue.calls, 0)

    def test_conflicting_agents_get_one_order(self):
        self.submit()
        self.submit(agentId="agent-2", proposalId="p-2", side="no", estimatedYesProbability=0.3)
        self.service.run_batch(self.s)
        outcomes = sorted((r["status"], r.get("decision")) for r in self.s["governorRequests"])
        self.assertEqual(outcomes, [("rejected", "rejected"), ("submitted", "approved")])
        self.assertEqual(self.venue.calls, 1)
        rejected = next(d for d in self.s["governorDecisions"] if d["decision"] == "rejected")
        self.assertEqual(rejected["reasonCodes"], [rules.CONFLICT])

    def test_price_moving_past_the_limit_skips_execution(self):
        self.venue.quote_override = 0.55
        view = self.run_one()
        self.assertEqual(view["status"], "skipped")
        self.assertIn("price moved", view["note"])
        self.assertEqual(self.venue.calls, 0)

    def test_stake_below_the_app_minimum_is_skipped(self):
        view = self.run_one(maxBudgetUsd=1)
        self.assertEqual((view["decision"]["decision"], view["status"]), ("approved", "skipped"))
        self.assertIn("$1", view["note"])

    def test_unknown_market_is_rejected_without_an_order(self):
        view = self.run_one(marketId="KXNOPE-1")
        self.assertEqual(view["decision"]["reasonCodes"], [rules.MARKET_NOT_IN_SNAPSHOT])
        self.assertEqual((view["status"], view["order"]), ("rejected", None))

    def test_existing_position_counts_and_blocks_the_market(self):
        self.venue.positions = [{"ticker": MARKET, "position_fp": "10.00", "market_exposure_dollars": "5.00"}]
        view = self.run_one()
        self.assertEqual(view["decision"]["reasonCodes"], [rules.POSITION_OPEN])

    def test_deleted_agent_is_rejected_instead_of_blocking_the_batch(self):
        self.submit()
        self.s["agentConfigs"][0]["status"] = "deleted"
        self.service.run_batch(self.s)
        self.assertEqual(self.s["governorRequests"][0]["status"], "rejected")
        self.assertEqual(self.s["governorBatches"], [])

    def test_loss_breaker_latches_blocks_and_resets(self):
        account = self.s["accounts"][0]
        self.workspace.refresh(self.s, account)  # day-open equity $1,000
        self.venue.cash = 960
        view = self.run_one()
        self.assertEqual(view["decision"]["reasonCodes"], [rules.LOSS_BREAKER])
        self.assertTrue(records.breaker_state(self.s, "account").latched)
        self.assertEqual(self.s["governorBreakerEvents"][0]["action"], "breakerLatched")
        self.assertTrue(any(e["kind"] == "BREAKER" for e in self.s["events"]))
        with self.assertRaises(ValueError):
            self.service.action(self.s, {"action": "governor-reset-breaker", "accountId": "account"})
        self.service.action(self.s, {"action": "governor-reset-breaker", "accountId": "account", "reason": "Reviewed the loss"})
        self.assertFalse(records.breaker_state(self.s, "account").latched)
        self.assertEqual(self.s["governorBreakerEvents"][0]["reason"], "Reviewed the loss")

    def test_browser_state_and_summary_stay_small(self):
        self.run_one()
        public = self.workspace.public(self.s)
        self.assertFalse([k for k in public if k.startswith("governor")])
        summary = json.loads(json.dumps(self.service.summary(self.s)))
        self.assertEqual(summary["queued"], 0)
        self.assertTrue(summary["recentDecisions"])
        self.assertTrue(all("trace" not in d for d in summary["recentDecisions"]))
        self.assertNotIn("inputs", json.dumps(summary))


class ScoringTests(ServiceTest):
    """Step 10: settled markets score every forecast and feed reliability back into sizing."""

    def test_adapter_reports_the_yes_settlement_value_only_once_settled(self):
        from adapters import KalshiAdapter
        cases = [({"status": "finalized", "result": "yes", "settlement_value_dollars": "1.0000"}, "1.0000"),
                 ({"status": "settled", "result": "no"}, "0"),
                 ({"status": "finalized", "result": "", "settlement_value_dollars": "0.5000"}, "0.5000"),
                 ({"status": "active", "result": ""}, None), ({"status": "determined", "result": "yes"}, None)]
        for market, expected in cases:
            adapter = KalshiAdapter({})
            adapter.request = lambda *a, market=market, **k: {"market": market}
            with self.subTest(market=market):
                self.assertEqual(adapter.get_market_outcome("M")["outcome"], expected)

    def test_settled_markets_score_forecasts_including_rejected_ones(self):
        self.submit()
        self.submit(agentId="agent-2", proposalId="p-2", side="no", estimatedYesProbability=0.3)  # loses the conflict
        self.submit(proposalId="p-3", marketId=OTHER, estimatedYesProbability=0.6)
        self.service.run_batch(self.s)
        self.venue.outcomes[MARKET] = "1.0000"
        self.assertEqual(self.service.score_forecasts(self.s), 2)
        scored = {f["agentId"]: f for f in self.s["governorForecasts"] if f["outcome"] is not None}
        self.assertEqual(set(scored), {"agent", "agent-2"})
        self.assertIsNone(next(f for f in self.s["governorForecasts"] if f["marketId"] == OTHER)["outcome"])
        self.assertEqual(self.service.score_forecasts(self.s), 0)  # already scored; never rewritten

    def test_reliability_feeds_back_into_the_next_sizing(self):
        def sized():
            code, body = self.submit(proposalId=f"size-{len(self.s['governorRequests'])}", marketId=OTHER,
                                     estimatedYesProbability=0.65, maxBudgetUsd=100)
            self.service.run_batch(self.s)
            return D(next(d for d in self.s["governorDecisions"] if d["proposalId"] == body["governorProposalId"])["trace"]["targetStake"])
        before = sized()
        for i in range(10):  # ten confident, correct calls on settled markets
            market = f"SETTLED-{i}"
            self.service.submit(self.s, submission(proposalId=f"hist-{i}", marketId=market, estimatedYesProbability=0.95))
            self.venue.outcomes[market] = "1.0000"
        self.service.run_batch(self.s)
        self.service.score_forecasts(self.s)
        self.s["orders"].clear()
        self.venue.positions = []
        self.assertGreater(records.reliability(self.s)["agent"].score, D("0.5"))
        self.assertGreater(sized(), before)

    def test_each_account_scores_only_its_own_agents(self):
        live = GovernedVenue()
        self.s["accounts"].append({**self.s["accounts"][0], "id": "live-account", "environment": "live"})
        self.s["agentConfigs"][1]["accountId"] = "live-account"
        self.workspace.venue = lambda account: live if account["id"] == "live-account" else self.venue
        self.submit()
        self.submit(agentId="agent-2", proposalId="p-2")
        self.venue.outcomes[MARKET] = "1.0000"  # demo settled YES; the live market has not settled
        self.assertEqual(self.service.score_forecasts(self.s), 1)
        outcomes = {f["agentId"]: f["outcome"] for f in self.s["governorForecasts"]}
        self.assertEqual(outcomes, {"agent": "1.0000", "agent-2": None})

    def test_unavailable_or_unreadable_results_are_skipped(self):
        self.submit()
        self.submit(proposalId="p-3", marketId=OTHER)
        self.venue.broken.add(MARKET)
        self.venue.outcomes[OTHER] = "not-a-number"
        self.assertEqual(self.service.score_forecasts(self.s), 0)
        self.venue.broken.clear()
        self.venue.outcomes.update({MARKET: "0", OTHER: "0.5000"})
        self.assertEqual(self.service.action(self.s, {"action": "governor-score"}), {"scored": 2})


class GovernedOrderTests(ServiceTest):
    """Orders buy exactly the governor's contracts at its limit price (live check: 4 approved had filled 3)."""

    def placed(self, order):
        from adapters import KalshiAdapter
        adapter, sent = KalshiAdapter({}), []
        adapter.request = lambda method, path, body=None, public=False: sent.append(body) or {"order_id": "x"}
        adapter.place_order(order)
        return sent[0]

    def test_place_order_sends_the_governed_count_and_limit(self):
        market = {"id": MARKET, "price": 0.37, "feeMultiplier": 1.0}
        yes = self.placed({"id": "o1", "market": market, "direction": "Yes", "amount": 1.5,
                           "governedTerms": {"contracts": "4", "limitPrice": "0.36"}})
        self.assertEqual((yes["count"], yes["price"], yes["side"], yes["time_in_force"]), ("4", "0.36", "bid", "immediate_or_cancel"))
        no = self.placed({"id": "o2", "market": market, "direction": "No", "amount": 1.5,
                          "governedTerms": {"contracts": "2", "limitPrice": "0.64"}})
        self.assertEqual((no["count"], no["price"], no["side"]), ("2", "0.36", "ask"))  # NO at 0.64 is YES at 0.36
        legacy = self.placed({"id": "o3", "market": {**market, "price": 0.5}, "direction": "Yes", "amount": 10})
        self.assertEqual(legacy["count"], "18")  # unchanged: $10 / (0.50 + 0.03 conservative fee reserve)

    def test_governed_terms_fit_the_cent_rounded_amount(self):
        from governor.fees import taker_fee
        code, body = self.submit()
        self.service.run_batch(self.s)
        order = next(o for o in self.s["orders"] if o.get("governorProposalId") == body["governorProposalId"])
        terms = order["governedTerms"]
        contracts, limit = D(terms["contracts"]), D(terms["limitPrice"])
        self.assertGreaterEqual(contracts, 1)
        self.assertLessEqual(limit * contracts + taker_fee(contracts, limit), D(str(order["amount"])))
        decision = next(d for d in self.s["governorDecisions"] if d["proposalId"] == body["governorProposalId"])
        self.assertLessEqual(contracts, D(decision["contracts"]))

    def test_workers_cannot_set_governed_terms(self):
        with self.assertRaisesRegex(ValueError, "Workers may only submit trade terms"):
            self.workspace.proposal(self.s, {"agentId": "agent", "marketId": MARKET, "direction": "Yes", "amount": 5,
                                             "reasoning": "x", "idempotencyKey": "k", "governedTerms": {"contracts": "99"}})


class ReviewFixTests(ServiceTest):
    """Follow-up review of the contract docs and the governor commits on main."""

    def test_retry_after_the_agent_is_paused_returns_the_original(self):
        code, body = self.submit()
        self.s["agentConfigs"][0]["status"] = "paused"
        again, view = self.submit()
        self.assertEqual((again, view["governorProposalId"]), (200, body["governorProposalId"]))
        self.assertEqual(self.submit(proposalId="p-new")[0], 400)  # new proposals still need an active agent

    def test_retry_beyond_the_window_is_recognised_in_json_mode(self):
        code, body = self.submit()
        self.service.run_batch(self.s)
        for i in range(3):
            self.submit(proposalId=f"later-{i}", marketId=OTHER, maxBudgetUsd=2)
            self.service.run_batch(self.s)
        with patch.object(records, "REQUEST_WINDOW", 1):
            compact = records.compact_for_json(self.s)
        slim = next(r for r in compact["governorRequests"] if r["proposalId"] == "p-1")
        self.assertNotIn("reasoning", slim)
        self.s = json.loads(json.dumps(compact))
        again, view = self.submit()
        self.assertEqual((again, view["governorProposalId"]), (200, body["governorProposalId"]))
        self.assertEqual(self.submit(maxBudgetUsd=21)[0], 409)

    def test_retry_beyond_the_window_uses_the_store_lookup(self):
        code, body = self.submit()
        stored = copy.deepcopy(self.s["governorRequests"][0])
        self.s["governorRequests"].clear()  # outside the loaded window
        self.service.find_request = lambda agent, pid: stored if (agent, pid) == ("agent", "p-1") else None
        again, view = self.submit()
        self.assertEqual((again, view["governorProposalId"]), (200, body["governorProposalId"]))

    def test_crash_mid_batch_leaves_no_queued_decision_and_resumes(self):
        self.submit()
        self.submit(agentId="agent-2", proposalId="p-2", marketId=OTHER)
        real_execute = self.service._execute
        self.service._execute = lambda *a: (_ for _ in ()).throw(RuntimeError("process killed"))
        with self.assertRaises(RuntimeError):
            self.service.run_batch(self.s)
        decided = {d["proposalId"] for d in self.s["governorDecisions"]}
        self.assertFalse([r for r in self.s["governorRequests"] if r["status"] == "queued" and r["governorProposalId"] in decided])
        self.assertEqual(sorted(r["status"] for r in self.s["governorRequests"]), ["approved", "approved"])
        self.service._execute = real_execute
        self.service.run_batch(self.s)  # resumes instead of failing on the recorded decisions
        self.assertEqual(sorted(r["status"] for r in self.s["governorRequests"]), ["submitted", "submitted"])
        self.assertEqual(self.venue.calls, 2)
        self.service.run_batch(self.s)
        self.assertEqual(self.venue.calls, 2)  # resuming twice never orders twice

    def test_expired_approved_request_is_skipped_on_resume(self):
        code, body = self.submit()
        request = self.request(body["governorProposalId"])
        self.service._execute = lambda *a: (_ for _ in ()).throw(RuntimeError("process killed"))
        with self.assertRaises(RuntimeError):
            self.service.run_batch(self.s)
        request["proposal"]["expiresAt"] = (utc_now() - timedelta(seconds=1)).isoformat()
        self.service.run_batch(self.s)
        self.assertEqual(request["status"], "skipped")
        self.assertIn("expired", request["note"])

    def test_paused_agents_queued_proposals_do_not_execute(self):
        self.submit()
        self.s["agentConfigs"][0]["status"] = "paused"
        self.service.run_batch(self.s)
        self.assertEqual((self.s["governorRequests"][0]["status"], self.venue.calls), ("rejected", 0))
        self.assertIn("paused", self.s["governorRequests"][0]["note"])

    def test_stakes_awaiting_user_approval_count_as_exposure(self):
        first = self.submit(maxBudgetUsd=100, estimatedYesProbability=0.8)[1]  # above the $25 automatic limit
        self.service.run_batch(self.s)
        self.assertEqual(self.service.lookup(self.s, first["governorProposalId"])["order"]["status"], "awaiting approval")
        second = self.submit(agentId="agent-2", proposalId="p-2")[1]
        self.service.run_batch(self.s)
        self.assertEqual(self.service.lookup(self.s, second["governorProposalId"])["decision"]["reasonCodes"], [rules.POSITION_OPEN])


class SigningTests(unittest.TestCase):
    """KalshiAdapter signs with RSA and Ed25519 keys (the live demo key is Ed25519)."""

    def sign_and_verify(self, private_key, verify):
        import base64
        from cryptography.hazmat.primitives import serialization
        from adapters import KalshiAdapter
        pem = private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()).decode()
        adapter = KalshiAdapter({"key_id": "key-1", "private_key": pem})
        headers = adapter.headers("GET", adapter.base + "/portfolio/balance?subaccount=0")
        message = (headers["KALSHI-ACCESS-TIMESTAMP"] + "GET" + "/trade-api/v2/portfolio/balance").encode()
        verify(private_key.public_key(), base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"]), message)
        self.assertEqual(headers["KALSHI-ACCESS-KEY"], "key-1")

    def test_ed25519_and_rsa_signatures_verify(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
        self.sign_and_verify(ed25519.Ed25519PrivateKey.generate(), lambda pub, sig, msg: pub.verify(sig, msg))
        self.sign_and_verify(rsa.generate_private_key(public_exponent=65537, key_size=2048), lambda pub, sig, msg: pub.verify(
            sig, msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256()))

    def test_other_key_types_are_refused(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from adapters import KalshiAdapter
        pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                                    serialization.NoEncryption()).decode()
        with self.assertRaisesRegex(VenueError, "RSA or Ed25519"):
            KalshiAdapter({"key_id": "k", "private_key": pem}).headers("GET", "https://example/trade-api/v2/x")


class HttpTests(unittest.TestCase):
    """The real request handler on a throwaway port, with JSON storage in a temp directory."""

    def setUp(self):
        import server
        self.server_module = server
        self.tmp = tempfile.TemporaryDirectory()
        Path(self.tmp.name, "main.json").write_text(json.dumps(workspace_state()))
        venue = GovernedVenue()
        workspace = Workspace(FakeVault())
        workspace.venue = lambda account: venue
        patches = [patch.object(server, "DATA", Path(self.tmp.name)), patch.object(server, "STORE", None),
                   patch.object(server, "GOVERNOR", GovernorService(workspace)),
                   patch.dict(os.environ, {"GOVERNTRADE_AGENT_TOKEN": "test-token"})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(self.tmp.cleanup)

    def call(self, method, path, body=None, token="test-token"):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        headers = {"Host": "localhost", "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        conn.request(method, path, body=None if body is None else (body if isinstance(body, str) else json.dumps(body)), headers=headers)
        response = conn.getresponse()
        data = json.loads(response.read() or b"{}")
        conn.close()
        return response.status, data

    def test_submit_lookup_schema_and_auth(self):
        self.assertEqual(self.call("POST", "/api/governor/proposals", submission(), token=None)[0], 403)
        self.assertEqual(self.call("GET", "/api/governor/schema", token="wrong")[0], 403)
        code, body = self.call("POST", "/api/governor/proposals", "{not json")
        self.assertEqual((code, body["schema"]), (400, "/api/governor/schema"))
        code, body = self.call("POST", "/api/governor/proposals", submission(side="maybe"))
        self.assertEqual((code, body["errors"][0]["field"]), (400, "side"))
        code, body = self.call("POST", "/api/governor/proposals", submission())
        self.assertEqual((code, body["status"]), (202, "queued"))
        code, view = self.call("GET", f"/api/governor/proposals/{body['governorProposalId']}")
        self.assertEqual((code, view["status"], view["proposalId"]), (200, "queued", "p-1"))
        self.assertEqual(self.call("GET", "/api/governor/proposals/nope")[0], 404)
        code, schema = self.call("GET", "/api/governor/schema")
        self.assertEqual((code, schema["jsonSchema"]["title"]), (200, "GovernedProposal"))
        code, summary = self.call("GET", "/api/governor/summary", token=None)
        self.assertEqual((code, summary["queued"]), (200, 1))
        stored = json.loads(Path(self.tmp.name, "main.json").read_text())
        self.assertEqual(stored["governorRequests"][0]["status"], "queued")


if __name__ == "__main__":
    unittest.main()
