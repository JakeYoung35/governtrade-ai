"""The in-app agent chat: model tool calls become governed proposals; provider message formats; the HTTP route."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import agent_chat
import agent_llm
from adapters import KalshiAdapter, VenueError
from agent_chat import LEGACY_REPLY, MAX_PROPOSALS, AgentChat
from agent_llm import ModelError, ModelTurn, ToolCall
from governor_service import GovernorService
from tests.python.test_governor_service import MARKET, GovernedVenue, workspace_state
from workspace import Workspace


class ChatVenue(GovernedVenue):
    """GovernedVenue plus the market discovery reads the agent's tools use."""

    def find_series(self, category, keywords=""):
        return [{"seriesTicker": "KXNFLGAME", "title": "NFL Game", "frequency": "custom"}]

    def list_markets(self, series_ticker):
        return [{"marketId": MARKET, "event": "BAL at CLE", "outcome": "Cleveland", "yesAsk": 0.52, "noAsk": 0.50,
                 "closeTime": None, "volume": 10.0}]

    def market_summary(self, market_id):
        if market_id not in self.books:
            raise VenueError("kalshi returned HTTP 404.")
        yes_ask, no_ask, depth = self.books[market_id]
        return {"marketId": market_id, "title": market_id, "outcome": None, "category": "sports",
                "yesAsk": float(yes_ask) if depth != "0" else None, "noAsk": float(no_ask) if depth != "0" else None,
                "closeTime": None, "open": True, "tradable": depth != "0", "rules": ""}


class KeyVault:
    def get(self, reference):
        return {"api_key": "sk-test-secret"}


class ScriptedModel:
    """Stands in for agent_llm.connect: replays model turns and records what the agent sent."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.results = []
        self.opened = None

    def __call__(self, provider, model, key, system, tools, history, post=None):
        self.opened = {"provider": provider, "model": model, "key": key, "system": system,
                       "tools": [t["name"] for t in tools], "history": history}
        return self

    def step(self):
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn

    def add_tool_results(self, results):
        self.results.append([result for _, result in results])


def calls(*items):
    return ModelTurn("", tuple(ToolCall(f"call-{i}", name, args) for i, (name, args) in enumerate(items)))


def propose(**overrides):
    args = {"marketId": MARKET, "side": "yes", "estimatedYesProbability": 0.65, "maxBudgetUsd": 20,
            "reasoning": "Cleveland is undervalued."}
    args.update(overrides)
    return ("propose_trade", args)


class ChatTest(unittest.TestCase):
    def setUp(self):
        self.s = workspace_state()
        for agent in self.s["agentConfigs"]:
            agent.update(provider="anthropic", model="claude-sonnet-5-5", modelCredentialRef="MODEL-REF")
        self.s["chats"] = {}
        self.venue = ChatVenue()
        self.workspace = Workspace(KeyVault())
        self.workspace.venue = lambda account: self.venue
        self.governor = GovernorService(self.workspace)
        self.workspace.refresh(self.s, self.s["accounts"][0])
        market_data = patch("agent_chat.public_market_data", lambda account: self.venue)  # the tools' public reads
        market_data.start()
        self.addCleanup(market_data.stop)

    def chat(self, model, text="Put $20 on Cleveland to win", agent="agent"):
        service = AgentChat(self.workspace, self.governor, connect=model)
        job = service.begin(self.s, {"agent": agent, "text": text})
        return service, service.finish(self.s, job, service.run(job))


class ProposalTests(ChatTest):
    def test_a_message_becomes_a_governed_proposal_that_executes(self):
        model = ScriptedModel(calls(("get_market", {"marketId": MARKET})), calls(propose()),
                              ModelTurn("I proposed $20 on Cleveland."))
        service, summary = self.chat(model)
        self.assertEqual(model.opened["key"], "sk-test-secret")
        self.assertEqual(model.opened["history"], [("user", "Put $20 on Cleveland to win")])
        self.assertIn("propose_trade", model.opened["tools"])
        self.assertEqual(model.results[1][0]["accepted"], True)
        self.assertEqual(model.results[1][0]["proposalId"], "chat-0-0")
        self.assertEqual([m["role"] for m in self.s["chats"]["agent"]], ["customer", "agent"])
        self.assertEqual(self.s["chats"]["agent"][1]["text"], "I proposed $20 on Cleveland.")
        request = self.s["governorRequests"][0]
        self.assertEqual((request["proposalId"], request["agentId"], request["status"]), ("chat-0-0", "agent", "queued"))
        self.assertEqual(summary["proposals"][0]["code"], 202)
        self.assertIn("chat proposal", self.s["events"][0]["text"])

        self.governor.run_batch(self.s)
        view = service.proposal_views(self.s)["agent"][0]
        self.assertEqual((view["messageIndex"], view["status"], view["maxBudgetUsd"]), (0, "submitted", "20"))
        self.assertEqual(view["order"]["status"], "filled")
        self.assertEqual(view["marketName"], "BTC")  # FakeVenue's quote name
        self.assertIsNone(view["blockReason"])
        # The browser state carries no evaluations, so a blocked order's reason travels with its proposal view.
        order = next(o for o in self.s["orders"] if o["id"] == view["order"]["id"])
        order.update(decision="BLOCK", status="blocked", evaluations=[{"outcome": "BLOCK", "checks": [
            {"name": "Your rules", "outcome": "ALLOW", "reason": ""},
            {"name": "Agent mandate", "outcome": "BLOCK", "reason": "Trade exceeds the agent daily limit."}]}])
        self.assertEqual(service.proposal_views(self.s)["agent"][0]["blockReason"],
                         "Agent mandate: Trade exceeds the agent daily limit.")

    def test_invalid_arguments_go_back_to_the_model_to_correct(self):
        model = ScriptedModel(calls(propose(estimatedYesProbability=1.5)), calls(propose()), ModelTurn("Done."))
        self.chat(model)
        self.assertFalse(model.results[0][0]["accepted"])
        self.assertEqual([e["field"] for e in model.results[0][0]["errors"]], ["estimatedYesProbability"])
        self.assertTrue(model.results[1][0]["accepted"])
        self.assertEqual(len(self.s["governorRequests"]), 1)

    def test_unknown_markets_and_empty_sides_are_refused(self):
        self.venue.books["KX-EMPTY"] = ("0.50", "0.50", "0")
        model = ScriptedModel(calls(propose(marketId="KX-MADE-UP"), propose(marketId="KX-EMPTY"),
                                    ("get_market", {"marketId": "KX-MADE-UP"})), ModelTurn("No luck."))
        self.chat(model)
        made_up, empty, lookup = model.results[0]
        self.assertIn("No Kalshi market KX-MADE-UP", made_up["error"])
        self.assertIn("cannot be traded", empty["error"])
        self.assertIn("No Kalshi market", lookup["error"])
        self.assertEqual(self.s.get("governorRequests", []), [])

    def test_duplicates_and_the_per_message_limit(self):
        markets = [f"KX-{i}" for i in range(MAX_PROPOSALS + 1)]
        for m in markets:
            self.venue.books[m] = ("0.40", "0.62", "100")
        model = ScriptedModel(calls(propose(maxBudgetUsd=5), propose(maxBudgetUsd=5),
                                    *[propose(marketId=m, maxBudgetUsd=5) for m in markets]), ModelTurn("Done."))
        self.chat(model, text="Spread $100 across the games")
        results = model.results[0]
        self.assertTrue(results[0]["accepted"])
        self.assertIn("already proposed", results[1]["error"])
        self.assertEqual(sum(r["accepted"] for r in results), MAX_PROPOSALS)
        self.assertIn(f"At most {MAX_PROPOSALS}", results[-1]["error"])
        self.assertEqual(sorted(r["proposalId"] for r in self.s["governorRequests"]),
                         sorted(f"chat-0-{i}" for i in range(MAX_PROPOSALS)))

    def test_browsing_tools_return_market_data(self):
        model = ScriptedModel(calls(("find_series", {"category": "Sports", "keywords": "nfl"}),
                                    ("list_markets", {"seriesTicker": "KXNFLGAME"}), ("list_markets", {}),
                                    ("not_a_tool", {})), ModelTurn("Here are the games."))
        self.chat(model, text="What NFL games can I bet on?")
        series, markets, missing, unknown = model.results[0]
        self.assertEqual(series["series"][0]["seriesTicker"], "KXNFLGAME")
        self.assertEqual(markets["markets"][0]["marketId"], MARKET)
        self.assertEqual(missing, {"error": "seriesTicker is required."})
        self.assertEqual(unknown, {"error": "Unknown tool not_a_tool."})
        self.assertEqual(self.s["chats"]["agent"][1]["text"], "Here are the games.")

    def test_later_messages_get_their_own_proposal_ids_and_history(self):
        self.s["chats"]["agent"] = [{"role": "customer", "text": "Hi"}, {"role": "agent", "text": LEGACY_REPLY + " old"},
                                    {"role": "customer", "text": "Anyone there?"}]
        model = ScriptedModel(calls(propose()), ModelTurn("Done."))
        self.chat(model)
        self.assertEqual(model.opened["history"], [("user", "Hi"), ("user", "Anyone there?"),
                                                   ("user", "Put $20 on Cleveland to win")])
        self.assertEqual(self.s["governorRequests"][0]["proposalId"], "chat-3-0")


class GuardTests(ChatTest):
    def test_a_paused_agent_can_talk_but_not_propose(self):
        self.s["agentConfigs"][0]["status"] = "paused"
        model = ScriptedModel(calls(propose()), ModelTurn("I'm paused."))
        self.chat(model)
        self.assertNotIn("propose_trade", model.opened["tools"])
        self.assertIn("the agent is paused", model.opened["system"])
        self.assertEqual(model.results[0], [{"error": "Unknown tool propose_trade."}])
        self.assertEqual(self.s.get("governorRequests", []), [])

    def test_trading_switched_off_and_no_mandate_block_proposals(self):
        self.s["permissions"]["automation"] = False
        model = ScriptedModel(ModelTurn("Trading is off."))
        self.chat(model)
        self.assertIn("switched off", model.opened["system"])
        self.s["permissions"]["automation"] = True
        self.s["agentConfigs"][1]["mandate"] = None
        model = ScriptedModel(ModelTurn("No mandate."))
        self.chat(model, agent="agent-2")
        self.assertIn("no saved mandate", model.opened["system"])

    def test_a_model_error_is_reported_and_frees_the_agent(self):
        model = ScriptedModel(ModelError("The model provider rejected the API key."))
        self.chat(model)
        self.assertEqual(self.s["chats"]["agent"][1]["text"], "I couldn't respond: The model provider rejected the API key.")
        self.assertNotIn("sk-test-secret", json.dumps(self.s))
        self.chat(ScriptedModel(ModelTurn("Back.")))  # not stuck as busy
        self.assertEqual(self.s["chats"]["agent"][-1]["text"], "Back.")

    def test_an_unexpected_tool_failure_goes_back_to_the_model(self):
        self.venue.find_series = lambda *a: 1 / 0  # e.g. a Kalshi payload the adapter cannot parse
        model = ScriptedModel(calls(propose(), ("find_series", {"category": "Sports"})), ModelTurn("Proposed Cleveland."))
        self.chat(model)
        self.assertEqual(model.results[0][1], {"error": "find_series failed (ZeroDivisionError). Try another market or series."})
        self.assertEqual(self.s["chats"]["agent"][1]["text"], "Proposed Cleveland.")
        self.assertEqual(self.s["governorRequests"][0]["proposalId"], "chat-0-0")

    def test_a_provider_error_after_a_proposal_keeps_the_proposal(self):
        model = ScriptedModel(calls(propose()), ModelError("The model provider is rate limiting or out of credit."))
        self.chat(model)
        self.assertEqual(self.s["chats"]["agent"][1]["text"],
                         "I couldn't respond: The model provider is rate limiting or out of credit. "
                         "I had already sent 1 proposal to the governor; see below.")
        self.assertEqual(self.s["governorRequests"][0]["status"], "queued")

    def test_a_disconnected_account_can_still_answer_questions(self):
        account = self.s["accounts"][0]
        account.update(status="disconnected", stale=True)
        account.pop("credentialRef")  # as Workspace's account-disconnect leaves it
        model = ScriptedModel(calls(("list_markets", {"seriesTicker": "KXNFLGAME"})), ModelTurn("Cleveland is 52 cents."))
        self.chat(model, text="What does Cleveland cost?")
        self.assertNotIn("propose_trade", model.opened["tools"])
        self.assertIn("not connected", model.opened["system"])
        self.assertEqual(model.results[0][0]["markets"][0]["marketId"], MARKET)
        self.assertEqual(self.s["chats"]["agent"][1]["text"], "Cleveland is 52 cents.")

    def test_a_late_release_cannot_free_the_next_turn(self):
        service = AgentChat(self.workspace, self.governor, connect=ScriptedModel(ModelTurn("ok")))
        first = service.begin(self.s, {"agent": "agent", "text": "first"})
        service.finish(self.s, first, service.run(first))
        second = service.begin(self.s, {"agent": "agent", "text": "second"})
        service.release("agent", first["turn"])  # the server's outer release for the first turn, arriving late
        with self.assertRaisesRegex(ValueError, "still working"):
            service.begin(self.s, {"agent": "agent", "text": "third"})
        service.release("agent", second["turn"])
        service.begin(self.s, {"agent": "agent", "text": "third"})

    def test_one_turn_at_a_time_per_agent(self):
        service = AgentChat(self.workspace, self.governor, connect=ScriptedModel(ModelTurn("ok")))
        job = service.begin(self.s, {"agent": "agent", "text": "first"})
        with self.assertRaisesRegex(ValueError, "still working"):
            service.begin(self.s, {"agent": "agent", "text": "second"})
        service.begin(self.s, {"agent": "agent-2", "text": "another agent is fine"})
        service.finish(self.s, job, service.run(job))
        service.begin(self.s, {"agent": "agent", "text": "now it's free"})

    def test_bad_requests_are_refused_before_anything_is_saved(self):
        service = AgentChat(self.workspace, self.governor, connect=ScriptedModel())
        for body, error in [({"agent": "agent", "text": " "}, "Type a message"),
                            ({"agent": "agent", "text": "x" * 2001}, "under 2000"),
                            ({"agent": "nobody", "text": "hi"}, "Choose an agent")]:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                service.begin(self.s, body)
        self.s["agentConfigs"][0].pop("modelCredentialRef")
        with self.assertRaisesRegex(ValueError, "model key"):
            service.begin(self.s, {"agent": "agent", "text": "hi"})
        self.assertEqual(self.s["chats"], {})

    def test_a_proposal_the_governor_refuses_is_explained_in_the_reply(self):
        service = AgentChat(self.workspace, self.governor, connect=ScriptedModel(calls(propose()), ModelTurn("Proposed.")))
        job = service.begin(self.s, {"agent": "agent", "text": "Buy Cleveland for $20"})
        result = service.run(job)
        self.s["agentConfigs"][0]["status"] = "paused"  # paused while the model was working
        summary = service.finish(self.s, job, result)
        self.assertEqual(summary["proposals"], [])
        self.assertIn("No proposal was sent because the agent is paused", self.s["chats"]["agent"][1]["text"])
        self.assertEqual(self.s.get("governorRequests", []), [])


class LimitTests(ChatTest):
    """Review fixes: the user's amount is enforced in code, replies fit the database, and a turn has a deadline."""

    def test_amounts_are_read_from_the_message(self):
        cases = {"Put $25 on it": ["25"], "25 dollars on PHI": ["25"], "bet 12.5 USD": ["12.5"], "ten bucks": [],
                 "Buy 3 contracts": [], "$0": [], "Use $5k on the Lakers": ["5000"], "Put $2 million on it": ["2000000"],
                 # Price levels are not stakes (review of 93a1733).
                 "Put $10 on the Eagles to win": ["10"],
                 "Put $10 on Bitcoin closing above $100,000 today": ["10"],
                 "Put $10 on the S&P 500 above $6,000 and $5 on Nasdaq": ["10", "5"],
                 "Bet $5 that gas averages over $3.50 this week": ["5"],
                 "Bet $20 on Bitcoin between $90,000 and $95,000": ["20"],
                 "$10 on BTC at $100k or above": ["10"],
                 "Is Bitcoin above $100,000?": [],
                 # No amount in a stake position: the smallest other amount, erring low.
                 "$1,000.50 and $3": ["3"], "Bitcoin to $100k, $10": ["10"]}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual([str(a) for a in agent_chat.stated_amounts(text)], expected)

    def test_price_targets_and_ambiguous_stakes_remain_conservative(self):
        cases = {
            "Put $10 on BTC hitting $120k in October": ["10"],
            "Will Bitcoin be worth $100,000 in December? Put $5 on yes": ["5"],
            "Bet $5 that gas is $4 for the week": ["5"],
            "Put $10 each on the Eagles and the Chiefs": ["10"],
            "Bitcoin closing under $90,000, $10 please": [],
            "Bitcoin hitting $120k in October": [],
            "Will Bitcoin be worth $100,000 in December?": [],
            "Gas is $4 for the week": [],
            "Put $10 on BTC reaching $120k": ["10"],
            "Put $10 on BTC crossing $120k": ["10"],
            "Put $10 on BTC topping $120k": ["10"],
            "Put $10 on BTC exceeding $120k": ["10"],
            "Put $5 on A and $50 on B": ["5"],
            "Put $5 on A and $10 on B": ["5", "10"],
            "Buy $10 worth of Cleveland": ["10"],
            "Buy Cleveland for $20": ["20"],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual([str(a) for a in agent_chat.stated_amounts(text)], expected)

    def test_gas_price_cannot_increase_the_total_budget(self):
        self.venue.books["KX-2"] = ("0.40", "0.62", "100")
        model = ScriptedModel(calls(propose(maxBudgetUsd=5), propose(marketId="KX-2", maxBudgetUsd=4)), ModelTurn("Done."))
        self.chat(model, text="Bet $5 that gas is $4 for the week")
        self.assertTrue(model.results[0][0]["accepted"])
        self.assertIn("Together these proposals exceed the $5", model.results[0][1]["error"])

    def test_hitting_price_cannot_raise_the_proposal_budget(self):
        model = ScriptedModel(calls(propose(maxBudgetUsd=50)), ModelTurn("Done."))
        self.chat(model, text="Put $10 on BTC hitting $120k in October")
        self.assertIn("above the $10 the user gave", model.results[0][0]["error"])
        self.assertEqual(self.s.get("governorRequests", []), [])

    def test_proposals_above_the_users_amount_are_refused(self):
        model = ScriptedModel(calls(propose(maxBudgetUsd=1000000), propose(maxBudgetUsd=20)), ModelTurn("Done."))
        self.chat(model, text="Put $20 on Cleveland")
        self.assertIn("above the $20 the user gave", model.results[0][0]["error"])
        self.assertTrue(model.results[0][1]["accepted"])
        self.assertEqual(self.s["governorRequests"][0]["proposal"]["requestedBudget"], "20")

    def test_proposals_together_stay_within_the_users_amounts(self):
        self.venue.books["KX-2"] = ("0.40", "0.62", "100")
        model = ScriptedModel(calls(propose(maxBudgetUsd=10), propose(marketId="KX-2", maxBudgetUsd=10)), ModelTurn("Done."))
        self.chat(model, text="$10 on Cleveland, and the rest elsewhere")  # one amount: $10 in total
        self.assertTrue(model.results[0][0]["accepted"])
        self.assertIn("Together these proposals exceed the $10", model.results[0][1]["error"])

    def test_a_price_level_in_the_message_does_not_raise_the_cap(self):
        model = ScriptedModel(calls(propose(maxBudgetUsd=50)), ModelTurn("Done."))
        self.chat(model, text="Put $10 on Bitcoin closing above $100,000 today")
        self.assertIn("above the $10 the user gave", model.results[0][0]["error"])
        self.assertIn("at most $10", model.opened["system"])

    def test_no_amount_means_no_proposal(self):
        model = ScriptedModel(calls(propose()), ModelTurn("How much?"))
        self.chat(model, text="Bet on Cleveland")
        self.assertIn("ask how much", model.opened["system"])
        self.assertIn("has not given a dollar amount", model.results[0][0]["error"])
        self.assertEqual(self.s.get("governorRequests", []), [])

    def test_an_answer_to_a_follow_up_uses_the_previous_amount(self):
        self.chat(ScriptedModel(ModelTurn("Which team?")), text="Put $20 on the Browns game")
        model = ScriptedModel(calls(propose()), ModelTurn("Done."))
        self.chat(model, text="Cleveland")
        self.assertTrue(model.results[0][0]["accepted"])
        self.assertIn("at most $20", model.opened["system"])

    def test_a_long_reply_is_trimmed_but_keeps_the_governor_notes(self):
        service = AgentChat(self.workspace, self.governor, connect=ScriptedModel(calls(propose()), ModelTurn("x" * 9000)))
        job = service.begin(self.s, {"agent": "agent", "text": "Put $20 on Cleveland"})
        result = service.run(job)
        self.governor.submit = lambda s, p: (400, {"error": "test refusal"})
        service.finish(self.s, job, result)
        reply = self.s["chats"]["agent"][1]["text"]
        self.assertLessEqual(len(reply), agent_chat.MAX_REPLY_CHARS)
        self.assertTrue(reply.endswith("test refusal."))
        self.assertIn("x…", reply)

    def test_a_turn_stops_at_its_deadline_and_keeps_what_it_proposed(self):
        clock = [0.0]

        class SlowModel(ScriptedModel):
            def step(self):
                clock[0] += 70  # each model call takes 70 s
                return super().step()

        model = SlowModel(calls(propose()), calls(("get_market", {"marketId": MARKET})), ModelTurn("never reached"))
        with patch("agent_chat.time.monotonic", lambda: clock[0]):
            self.chat(model)
        self.assertEqual(self.s["chats"]["agent"][1]["text"],
                         "I couldn't respond: The agent took too long and stopped. "
                         "I had already sent 1 proposal to the governor; see below.")
        self.assertEqual(self.s["governorRequests"][0]["proposalId"], "chat-0-0")

    def test_retries_stop_when_the_turn_is_nearly_over(self):
        clock, sent = [0.0], []

        def post_json(url, headers, body, timeout):
            sent.append(timeout)
            clock[0] = 110  # the first call used most of the turn
            raise ModelError("The model provider is overloaded right now (HTTP 503). Try again in a minute.", retryable=True)

        def connect(provider, model, key, system, tools, history, post):
            with patch("agent_chat.post_json", post_json), self.assertRaisesRegex(ModelError, "overloaded"):
                post("https://api.example", {}, {})
            raise ModelError("stop here")

        with patch("agent_chat.time.monotonic", lambda: clock[0]), patch("agent_chat.time.sleep") as sleep:
            self.chat(connect)
        self.assertEqual((len(sent), sleep.call_count), (1, 0))  # 10 s left is too little to wait and retry

    def test_each_model_call_gets_only_the_time_left(self):
        clock, seen = [0.0], []

        def connect(provider, model, key, system, tools, history, post):
            clock[0] = 100  # 20 s of the 120 s turn left
            with patch("agent_chat.post_json", lambda url, headers, body, timeout: seen.append(timeout) or {}):
                post("https://api.example", {}, {})
            clock[0] = 119.5
            with self.assertRaisesRegex(ModelError, "took too long"):
                post("https://api.example", {}, {})
            raise ModelError("stop here")

        with patch("agent_chat.time.monotonic", lambda: clock[0]):
            self.chat(connect)
        self.assertEqual(seen, [20.0])

    def test_proposal_views_match_single_lookups(self):
        self.chat(ScriptedModel(calls(propose()), ModelTurn("Done.")))
        self.governor.run_batch(self.s)
        request = self.s["governorRequests"][0]
        self.assertEqual(self.governor.views(self.s, [request]), [self.governor.lookup(self.s, request["governorProposalId"])])


    def test_natural_budget_phrases(self):
        for text, amount in [("Spend up to $50 on the Eagles", 50), ("Put $20 or less on the Chiefs", 20),
                             ("Put around $15 on the Lakers", 15), ("Put about $15 on the Lakers", 15),
                             ("My budget is $20", 20), ("My limit is $25", 25)]:
            with self.subTest(text=text):
                self.assertEqual(agent_chat.stated_amounts(text), [amount])

    def test_spent_allowance_is_not_reused_after_reload(self):
        self.chat(ScriptedModel(calls(propose()), ModelTurn("Done.")))
        self.s = json.loads(json.dumps(self.s))
        self.s["governorRequests"] = []  # even after the bounded request history is compacted
        model = ScriptedModel(calls(propose()), ModelTurn("How much?"))
        self.chat(model, text="great, also do the Chiefs")
        self.assertIn("has not given a dollar amount", model.results[0][0]["error"])
        self.assertEqual(self.s["governorRequests"], [])

    def test_followup_allowance_survives_reload_but_not_interrupted_turn(self):
        self.chat(ScriptedModel(ModelTurn("Which team?")))
        self.s = json.loads(json.dumps(self.s))
        service = AgentChat(self.workspace, self.governor)
        job = service.begin(self.s, {"agent": "agent", "text": "Philadelphia"})
        self.assertEqual(job["amounts"], [20])
        service.release(job["agentId"], job["turn"])  # simulate an interrupted turn
        self.s = json.loads(json.dumps(self.s))
        job = service.begin(self.s, {"agent": "agent", "text": "also Cleveland"})
        self.assertEqual(job["amounts"], [])

    def test_submit_failure_keeps_other_proposals_and_reply(self):
        self.venue.books["KX-2"] = ("0.40", "0.62", "100")
        original = self.governor.submit
        def submit(state, payload):
            if payload["marketId"] == "KX-2":
                raise RuntimeError("secret database details")
            return original(state, payload)
        self.governor.submit = submit
        model = ScriptedModel(calls(propose(maxBudgetUsd=10), propose(marketId="KX-2", maxBudgetUsd=10)), ModelTurn("Done."))
        self.chat(model)
        self.assertEqual(len(self.s["governorRequests"]), 1)
        reply = self.s["chats"]["agent"][-1]["text"]
        self.assertIn("could not be confirmed", reply)
        self.assertNotIn("secret", reply)
        self.assertNotIn("agent", self.s["chatAllowances"])

    def test_pausing_all_trading_midturn_does_not_submit(self):
        service = AgentChat(self.workspace, self.governor, connect=ScriptedModel(calls(propose()), ModelTurn("Sent!")))
        job = service.begin(self.s, {"agent": "agent", "text": "Put $20 on Cleveland"})
        result = service.run(job)
        self.s["permissions"]["automation"] = False
        with patch.object(self.governor, "submit") as submit:
            service.finish(self.s, job, result)
            submit.assert_not_called()
        self.assertNotIn("Sent!", self.s["chats"]["agent"][-1]["text"])

    def test_oversized_notes_still_fit_reply_limit(self):
        self.governor.submit = lambda s, p: (400, {"error": "x" * 9000})
        self.chat(ScriptedModel(calls(propose()), ModelTurn("Done.")))
        self.assertLessEqual(len(self.s["chats"]["agent"][-1]["text"]), agent_chat.MAX_REPLY_CHARS)

    def test_deadline_stops_further_tools_in_one_model_step(self):
        clock, seen = [0.0], []
        def summary(ticker):
            seen.append(ticker)
            clock[0] += 121
            return {}
        self.venue.market_summary = summary
        model = ScriptedModel(calls(("get_market", {"marketId": "A"}), ("get_market", {"marketId": "B"})))
        with patch("agent_chat.time.monotonic", lambda: clock[0]):
            self.chat(model)
        self.assertEqual(seen, ["A"])
        self.assertIn("took too long", self.s["chats"]["agent"][-1]["text"])


class ProviderTests(unittest.TestCase):
    """Each provider's request and response format, with a recorded post() instead of the network."""

    TOOLS = [{"name": "get_market", "description": "d", "parameters": {
        "type": "object", "required": ["marketId"], "properties": {"marketId": {"type": "string"}}}}]

    def recorder(self, *responses):
        sent = []
        replies = list(responses)

        def post(url, headers, body):
            sent.append((url, headers, json.loads(json.dumps(body))))  # a snapshot, as sent at the time
            return replies.pop(0)
        return sent, post

    def test_anthropic(self):
        sent, post = self.recorder(
            {"content": [{"type": "text", "text": "Checking. "}, {"type": "tool_use", "id": "tu_1", "name": "get_market",
                                                                  "input": {"marketId": "KX"}}]},
            {"content": [{"type": "text", "text": "Done."}]})
        conversation = agent_llm.connect("anthropic", "claude-sonnet-5-5", "key", "system", self.TOOLS,
                                         [("assistant", "hello"), ("user", "a"), ("user", "b")], post=post)
        turn = conversation.step()
        self.assertEqual((turn.text, turn.tool_calls), ("Checking.", (ToolCall("tu_1", "get_market", {"marketId": "KX"}),)))
        conversation.add_tool_results([(turn.tool_calls[0], {"title": "T"})])
        self.assertEqual(conversation.step().text, "Done.")
        url, headers, body = sent[1]
        self.assertEqual((url, headers["x-api-key"]), ("https://api.anthropic.com/v1/messages", "key"))
        self.assertEqual(body["system"], [{"type": "text", "text": "system", "cache_control": {"type": "ephemeral"}}])
        self.assertEqual((body["max_tokens"], body["tools"][0]["input_schema"]["required"]), (2048, ["marketId"]))
        self.assertEqual(body["messages"][0], {"role": "user", "content": "a\n\nb"})  # merged; leading reply dropped
        self.assertEqual(body["messages"][2]["content"][0],
                         {"type": "tool_result", "tool_use_id": "tu_1", "content": json.dumps({"title": "T"})})

    def test_openai(self):
        sent, post = self.recorder(
            {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "get_market", "arguments": "{\"marketId\": \"KX\"}"}},
                {"id": "c2", "type": "function", "function": {"name": "get_market", "arguments": "not json"}}]}}]},
            {"choices": [{"message": {"role": "assistant", "content": "Done."}}]})
        conversation = agent_llm.connect("openai", "gpt-5", "key", "system", self.TOOLS, [("user", "a")], post=post)
        turn = conversation.step()
        self.assertEqual([c.arguments for c in turn.tool_calls], [{"marketId": "KX"}, {}])
        conversation.add_tool_results([(c, {"ok": True}) for c in turn.tool_calls])
        self.assertEqual(conversation.step().text, "Done.")
        url, headers, body = sent[1]
        self.assertEqual((url, headers["Authorization"]), ("https://api.openai.com/v1/chat/completions", "Bearer key"))
        self.assertEqual(body["messages"][0], {"role": "system", "content": "system"})
        self.assertEqual(body["max_completion_tokens"], agent_llm.MAX_OUTPUT_TOKENS_WITH_REASONING)
        self.assertEqual(body["tools"][0]["function"]["name"], "get_market")
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user", "assistant", "tool", "tool"])
        self.assertEqual(body["messages"][3]["tool_call_id"], "c1")

    def test_gemini_keeps_thought_signatures(self):
        call_part = {"functionCall": {"name": "get_market", "args": {"marketId": "KX"}}, "thoughtSignature": "sig"}
        sent, post = self.recorder({"candidates": [{"content": {"role": "model", "parts": [call_part]}}]},
                                   {"candidates": [{"content": {"parts": [{"text": "thinking", "thought": True},
                                                                          {"text": "Done."}]}}]})
        conversation = agent_llm.connect("google", "gemini-3.5-flash", "key", "system", self.TOOLS, [("user", "a")], post=post)
        turn = conversation.step()
        self.assertEqual(turn.tool_calls[0].arguments, {"marketId": "KX"})
        conversation.add_tool_results([(turn.tool_calls[0], {"title": "T"})])
        self.assertEqual(conversation.step().text, "Done.")
        url, headers, body = sent[1]
        self.assertTrue(url.endswith("/models/gemini-3.5-flash:generateContent"))
        self.assertEqual(headers["x-goog-api-key"], "key")
        self.assertEqual(body["tools"][0]["functionDeclarations"][0]["parameters"]["type"], "OBJECT")
        self.assertEqual(body["generationConfig"], {"maxOutputTokens": agent_llm.MAX_OUTPUT_TOKENS_WITH_REASONING})
        self.assertEqual(body["contents"][1]["parts"][0], call_part)
        self.assertEqual(body["contents"][2]["parts"][0]["functionResponse"], {"name": "get_market", "response": {"title": "T"}})

    def test_openai_keeps_only_the_calls_it_answers(self):
        sent, post = self.recorder(
            {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "get_market", "arguments": "{}"}},
                {"id": "c2", "type": "custom", "custom": {"name": "x", "input": ""}}]}}]},
            {"choices": [{"message": {"role": "assistant", "content": "Done."}}]})
        conversation = agent_llm.connect("openai", "gpt-5", "key", "system", self.TOOLS, [("user", "a")], post=post)
        turn = conversation.step()
        self.assertEqual([c.id for c in turn.tool_calls], ["c1"])
        conversation.add_tool_results([(turn.tool_calls[0], {})])
        conversation.step()
        assistant = sent[1][2]["messages"][2]
        self.assertEqual([c["id"] for c in assistant["tool_calls"]], ["c1"])  # every kept call has a tool response

    def test_malformed_tool_calls_are_model_errors(self):
        sent, post = self.recorder({"content": [{"type": "tool_use", "name": "get_market"}]})  # no id
        conversation = agent_llm.connect("anthropic", "m", "key", "system", self.TOOLS, [("user", "a")], post=post)
        with self.assertRaisesRegex(ModelError, "unreadable"):
            conversation.step()

    def test_gemini_without_candidates_is_a_model_error(self):
        sent, post = self.recorder({"promptFeedback": {"blockReason": "SAFETY"}})
        conversation = agent_llm.connect("google", "gemini-3.5-flash", "key", "system", [], [("user", "a")], post=post)
        with self.assertRaisesRegex(ModelError, "safety filter"):
            conversation.step()

    def test_http_errors_never_include_the_key_or_body(self):
        error = HTTPError("https://api.example", 401, "Unauthorized", {}, None)
        with patch("agent_llm.urlopen", side_effect=error):
            with self.assertRaises(ModelError) as caught:
                agent_llm.post_json("https://api.example", {"x-api-key": "sk-secret"}, {})
        self.assertEqual(str(caught.exception), "The model provider rejected the API key.")
        with self.assertRaisesRegex(ModelError, "not supported"):
            agent_llm.connect("other", "m", "k", "s", [], [])

    def test_credit_and_billing_errors_are_actionable_without_echoing_provider_text(self):
        import io
        for status, payload, expected in [
            (400, {"error": {"message": "Your credit balance is too low to access the Anthropic API. sk-secret"}}, "out of API credit"),
            (429, {"error": {"code": "insufficient_quota", "message": "sk-secret"}}, "spending limit"),
            (400, {"error": {"message": "Check your billing details. sk-secret"}}, "billing issue"),
            (400, {"error": {"message": "Invalid request: sk-secret"}}, "rejected the request"),
            (429, {"error": {"message": "Too many requests"}}, "rate limiting"),
        ]:
            with self.subTest(status=status, payload=payload):
                error = HTTPError("https://api.example", status, "error", {}, io.BytesIO(json.dumps(payload).encode()))
                with patch("agent_llm.urlopen", side_effect=error), self.assertRaises(ModelError) as caught:
                    agent_llm.post_json("https://api.example", {"x-api-key": "sk-secret"}, {})
                self.assertIn(expected, str(caught.exception))
                self.assertNotIn("sk-secret", str(caught.exception))

    def test_outages_are_retryable_with_a_clear_message_and_refusals_are_not(self):
        import io
        from urllib.error import URLError
        for error, expected, retryable in [
            (HTTPError("https://api.example", 503, "x", {}, None), "is overloaded right now (HTTP 503). Try again in a minute.", True),
            (HTTPError("https://api.example", 500, "x", {}, None), "had a temporary problem (HTTP 500). Try again in a minute.", True),
            (HTTPError("https://api.example", 429, "x", {}, io.BytesIO(b'{"error":{"message":"slow down"}}')), "rate limiting", True),
            (HTTPError("https://api.example", 429, "x", {}, io.BytesIO(b'{"error":{"code":"insufficient_quota"}}')), "spending limit", False),
            (HTTPError("https://api.example", 401, "x", {}, None), "rejected the API key", False),
            (URLError(ConnectionRefusedError()), "could not be reached", True),
            (URLError(TimeoutError()), "could not be reached", False),
            (TimeoutError(), "could not be reached", False),
        ]:
            with self.subTest(error=error), patch("agent_llm.urlopen", side_effect=error):
                with self.assertRaises(ModelError) as caught:
                    agent_llm.post_json("https://api.example/v1/models/m?key=sk-secret", {}, {})
                self.assertIn(expected, str(caught.exception))
                self.assertEqual(caught.exception.retryable, retryable)

    def test_provider_statuses_are_logged_without_the_url_path_or_key(self):
        import contextlib, io
        out = io.StringIO()
        with patch("agent_llm.urlopen", side_effect=HTTPError("u", 503, "x", {}, None)), contextlib.redirect_stdout(out):
            with self.assertRaises(ModelError):
                agent_llm.post_json("https://generativelanguage.googleapis.com/v1beta/models/m:generateContent?key=sk-secret", {}, {})
        self.assertEqual(out.getvalue(), "Model provider generativelanguage.googleapis.com returned HTTP 503.\n")

    def test_unreadable_error_bodies_keep_status_fallback(self):
        import io
        for body in (b"", b"not json", b"[]", b'{"error":null}', b"x" * 9000):
            with self.subTest(body=body[:30]):
                error = HTTPError("https://api.example", 400, "error", {}, io.BytesIO(body))
                with patch("agent_llm.urlopen", side_effect=error), self.assertRaisesRegex(ModelError, "rejected the request"):
                    agent_llm.post_json("https://api.example", {}, {})


class AdapterTests(unittest.TestCase):
    """Market discovery tolerates nulls and missing fields in Kalshi payloads."""

    def test_null_titles_missing_tickers_and_empty_asks(self):
        venue = KalshiAdapter({}, "sandbox")
        responses = {
            "/series": {"series": [{"ticker": "KXNFLGAME", "title": None, "tags": None}, {"title": "no ticker"}, "junk"]},
            "/events": {"events": [{"title": None, "markets": [
                {"ticker": "KX-A", "status": "active", "yes_ask_dollars": "", "no_ask_dollars": "0.40", "title": None},
                {"ticker": "KX-B", "status": "active", "yes_ask_dollars": None, "no_ask_dollars": None}]}]},
        }
        venue.request = lambda method, path, public=False: responses[path.split("?")[0]]
        self.assertEqual(venue.find_series("Sports", "nfl"), [{"seriesTicker": "KXNFLGAME", "title": "", "frequency": None}])
        self.assertEqual(venue.list_markets("KXNFLGAME"), [{"marketId": "KX-A", "event": "", "outcome": "", "yesAsk": None,
                                                            "noAsk": 0.4, "closeTime": None, "volume": 0.0}])

    def test_market_summary_uses_canonical_ticker(self):
        venue = KalshiAdapter({}, "sandbox")
        venue._market = lambda ticker: ({"ticker": "KX-A", "status": "active", "yes_ask_dollars": "0.4"}, "sports", 1, True)
        self.assertEqual(venue.market_summary("kx-a")["marketId"], "KX-A")

    def test_unknown_close_times_sort_last(self):
        venue = KalshiAdapter({}, "sandbox")
        venue.request = lambda *a, **kw: {"events": [{"markets": [
            {"ticker": ticker, "status": "active", "yes_ask_dollars": "0.4", "close_time": close}
            for ticker, close in [("UNKNOWN", None), ("LATE", "2026-11-01"), ("EARLY", "2026-10-01")]]}]}
        self.assertEqual([m["marketId"] for m in venue.list_markets("KX")], ["EARLY", "LATE", "UNKNOWN"])

    def test_each_venue_request_gets_remaining_time(self):
        import io
        venue = KalshiAdapter({}, "sandbox")
        venue.deadline = 120
        with patch("adapters.time.monotonic", return_value=118), patch("adapters.urlopen", return_value=io.BytesIO(b'{}')) as request:
            venue.request("GET", "/markets", public=True)
            self.assertEqual(request.call_args.kwargs["timeout"], 2)
        with patch("adapters.time.monotonic", return_value=120), patch("adapters.urlopen") as request:
            with self.assertRaisesRegex(VenueError, "deadline"):
                venue.request("GET", "/markets", public=True)
            request.assert_not_called()


class RequestIdTests(ChatTest):
    """A send carries a client request ID so a retry after a lost response never runs the message twice."""

    def begin(self, service, text="Put $20 on Cleveland", request_id="req-0001"):
        return service.begin(self.s, {"agent": "agent", "text": text, "requestId": request_id})

    def test_a_retry_after_the_turn_finished_does_not_run_again(self):
        service = AgentChat(self.workspace, self.governor, connect=ScriptedModel(calls(propose()), ModelTurn("Proposed.")))
        job = self.begin(service)
        service.finish(self.s, job, service.run(job))
        retry = self.begin(service)  # the response was lost after the message was stored; the browser sends again
        self.assertEqual(retry, {"duplicate": True, "agentId": "agent", "messageIndex": 0})
        self.assertEqual([m["role"] for m in self.s["chats"]["agent"]], ["customer", "agent"])
        self.assertEqual(len(self.s["governorRequests"]), 1)
        self.begin(service, "Put $20 on Cleveland", "req-0002")  # a new ID is a new message, even with the same text
        self.assertEqual(len(self.s["chats"]["agent"]), 3)

    def test_a_retry_while_the_first_turn_is_still_running_is_not_refused(self):
        service = AgentChat(self.workspace, self.governor, connect=ScriptedModel(ModelTurn("ok")))
        job = self.begin(service)
        self.assertTrue(self.begin(service)["duplicate"])  # not "still working": the message is already stored
        with self.assertRaisesRegex(ValueError, "still working"):
            self.begin(service, "another message", "req-0002")
        service.finish(self.s, job, service.run(job))

    def test_a_retry_is_recognised_even_after_the_agent_key_changed(self):
        # A refusal would tell the browser the message was never stored, and it would put it back in the box.
        service = AgentChat(self.workspace, self.governor, connect=ScriptedModel(ModelTurn("ok")))
        job = self.begin(service)
        service.finish(self.s, job, service.run(job))
        self.s["agentConfigs"][0]["modelValidated"] = False
        self.assertEqual(self.begin(service), {"duplicate": True, "agentId": "agent", "messageIndex": 0})
        with self.assertRaisesRegex(ValueError, "Validate this agent's model key"):
            self.begin(service, "Put $20 on Cleveland", "req-0002")

    def test_a_request_that_was_never_stored_runs_when_sent_again(self):
        service = AgentChat(self.workspace, self.governor, connect=ScriptedModel(ModelTurn("first"), ModelTurn("second")))
        running = service.begin(self.s, {"agent": "agent", "text": "first"})
        with self.assertRaisesRegex(ValueError, "still working"):  # refused before anything was stored
            self.begin(service, "Put $20 on Cleveland", "req-0009")
        service.finish(self.s, running, service.run(running))
        job = self.begin(service, "Put $20 on Cleveland", "req-0009")
        self.assertNotIn("duplicate", job)
        service.finish(self.s, job, service.run(job))
        self.assertEqual([m["text"] for m in self.s["chats"]["agent"]], ["first", "first", "Put $20 on Cleveland", "second"])

    def test_invalid_request_ids_are_refused_before_anything_is_saved(self):
        service = AgentChat(self.workspace, self.governor, connect=ScriptedModel())
        for bad in ["short", "x" * 65, "has space 123", 12345678, ""]:
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "request ID"):
                self.begin(service, request_id=bad)
        self.assertEqual(self.s["chats"], {})
        self.assertNotIn("chatRequests", self.s)

    def test_only_recent_request_ids_are_kept_and_they_stay_private(self):
        service = AgentChat(self.workspace, self.governor, connect=ScriptedModel(*[ModelTurn("ok")] * (agent_chat.MAX_CHAT_REQUESTS + 2)))
        for i in range(agent_chat.MAX_CHAT_REQUESTS + 2):
            job = self.begin(service, f"message {i}", f"req-{i:04d}")
            service.finish(self.s, job, service.run(job))
        kept = self.s["chatRequests"]["agent"]
        self.assertEqual(len(kept), agent_chat.MAX_CHAT_REQUESTS)
        self.assertEqual(kept[0], {"id": "req-0002", "messageIndex": 4})
        self.assertNotIn("chatRequests", self.workspace.public(self.s))


class HttpTests(unittest.TestCase):
    """The chat route on the real handler: the model runs with the workspace lock released."""

    def setUp(self):
        import server
        self.server = server
        self.tmp = tempfile.TemporaryDirectory()
        s = workspace_state()
        for agent in s["agentConfigs"]:
            agent.update(provider="anthropic", model="claude-sonnet-5-5", modelCredentialRef="MODEL-REF")
        self.venue = ChatVenue()
        workspace = Workspace(KeyVault())
        workspace.venue = lambda account: self.venue
        workspace.refresh(s, s["accounts"][0])
        Path(self.tmp.name, "main.json").write_text(json.dumps(s))
        governor = GovernorService(workspace)
        self.chat = AgentChat(workspace, governor, market_data=lambda account: self.venue)
        patches = [patch.object(server, "DATA", Path(self.tmp.name)), patch.object(server, "STORE", None),
                   patch.object(server, "WORKSPACE", workspace), patch.object(server, "GOVERNOR", governor),
                   patch.object(server, "AGENT_CHAT", self.chat)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(self.tmp.cleanup)

    def call(self, method, path, body=None, token=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        headers = {"Host": "localhost", "Content-Type": "application/json", "X-CSRF-Token": self.server.CSRF,
                   "Cookie": f"governtrade={self.server.SESSION}", **({"Authorization": f"Bearer {token}"} if token else {})}
        conn.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
        response = conn.getresponse()
        data = json.loads(response.read() or b"{}")
        conn.close()
        return response.status, data

    def test_chat_proposes_while_the_lock_is_free(self):
        polled = []
        test = self

        class PollingModel(ScriptedModel):
            def step(self):
                if not polled:  # a request made during the model call must not wait for the chat request
                    polled.append(test.call("GET", "/api/chat/proposals"))
                return super().step()

        self.chat.connect = PollingModel(calls(propose()), ModelTurn("Proposed $20 on Cleveland."))
        code, body = self.call("POST", "/api/action", {"action": "chat", "agent": "agent", "text": "Put $20 on Cleveland"})
        self.assertEqual(code, 200)
        self.assertEqual(polled, [(200, {"chatProposals": {}})])
        self.assertEqual(body["result"]["proposals"][0]["code"], 202)
        self.assertEqual([m["role"] for m in body["state"]["chats"]["agent"]], ["customer", "agent"])
        self.assertEqual(body["state"]["chatProposals"]["agent"][0]["status"], "queued")
        code, light = self.call("GET", "/api/chat/proposals")
        self.assertEqual(light["chatProposals"]["agent"][0]["proposalId"], "chat-0-0")
        self.assertNotIn("sk-test-secret", Path(self.tmp.name, "main.json").read_text())

    def test_a_resend_during_or_after_the_turn_is_stored_once(self):
        retries = []
        test = self
        send = {"action": "chat", "agent": "agent", "text": "Put $20 on Cleveland", "requestId": "req-http-1"}

        class ResendingModel(ScriptedModel):
            def step(self):
                if not retries:  # the browser lost the response and sends again while the turn is still running
                    retries.append(test.call("POST", "/api/action", send))
                return super().step()

        self.chat.connect = ResendingModel(calls(propose()), ModelTurn("Proposed $20 on Cleveland."))
        code, body = self.call("POST", "/api/action", send)
        self.assertEqual(code, 200)
        (during_code, during), = retries
        self.assertEqual((during_code, during["result"]), (200, {"duplicate": True, "messageIndex": 0}))
        self.assertEqual([m["role"] for m in body["state"]["chats"]["agent"]], ["customer", "agent"])
        code, after = self.call("POST", "/api/action", send)  # and again once the turn is over
        self.assertEqual((code, after["result"]), (200, {"duplicate": True, "messageIndex": 0}))
        self.assertEqual(len(after["state"]["chats"]["agent"]), 2)
        self.assertEqual(len(after["state"]["chatProposals"]["agent"]), 1)
        self.assertNotIn("chatRequests", after["state"])
        stored = json.loads(Path(self.tmp.name, "main.json").read_text())
        self.assertEqual(stored["chatRequests"], {"agent": [{"id": "req-http-1", "messageIndex": 0}]})

    def test_workers_get_no_chat_instructions_and_cannot_use_chat_ids(self):
        with patch.dict("os.environ", {"GOVERNTRADE_AGENT_TOKEN": "worker-token"}):
            self.chat.connect = ScriptedModel(ModelTurn("Hi."))
            self.call("POST", "/api/action", {"action": "chat", "agent": "agent", "text": "Put $20 on Cleveland"})
            code, body = self.call("GET", "/api/worker", token="worker-token")
            self.assertEqual((code, body["instructions"], body["chatHandledBy"]), (200, {}, "app"))
            payload = {"schemaVersion": 1, "proposalId": "chat-0-0", "agentId": "agent", "marketId": MARKET, "side": "yes",
                       "estimatedYesProbability": 0.6, "maxBudgetUsd": 5, "reasoning": "r"}
            code, body = self.call("POST", "/api/governor/proposals", payload, token="worker-token")
            self.assertEqual((code, body["errors"][0]["field"]), (400, "proposalId"))
            code, body = self.call("POST", "/api/governor/proposals", {**payload, "proposalId": "w-1"}, token="worker-token")
            self.assertEqual(code, 202)

    def test_bad_chat_requests_are_400s(self):
        self.chat.connect = ScriptedModel()
        self.assertEqual(self.call("POST", "/api/action", {"action": "chat", "agent": "agent", "text": ""})[0], 400)
        self.assertEqual(self.call("POST", "/api/action", {"action": "chat", "agent": "nobody", "text": "hi"})[0], 400)


if __name__ == "__main__":
    unittest.main()
