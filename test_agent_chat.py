"""The in-app agent chat: model tool calls become governed proposals; provider message formats; the HTTP route."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import agent_llm
from adapters import VenueError
from agent_chat import LEGACY_REPLY, MAX_PROPOSALS, AgentChat
from agent_llm import ModelError, ModelTurn, ToolCall
from governor_service import GovernorService
from test_governor_service import MARKET, GovernedVenue, workspace_state
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

    def __call__(self, provider, model, key, system, tools, history):
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
        model = ScriptedModel(calls(propose(), propose(), *[propose(marketId=m) for m in markets]), ModelTurn("Done."))
        self.chat(model)
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

    def test_an_unexpected_tool_failure_is_contained(self):
        self.venue.find_series = lambda *a: 1 / 0
        model = ScriptedModel(calls(("find_series", {"category": "Sports"})))
        self.chat(model)
        self.assertEqual(self.s["chats"]["agent"][1]["text"],
                         "I couldn't respond: The agent hit an unexpected error (ZeroDivisionError).")

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
        self.assertEqual(summary["proposals"][0]["code"], 400)
        self.assertIn("did not accept the " + MARKET, self.s["chats"]["agent"][1]["text"])
        self.assertEqual(self.s.get("governorRequests", []), [])


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
        self.assertEqual((body["system"], body["tools"][0]["input_schema"]["required"]), ("system", ["marketId"]))
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
        self.assertEqual(body["contents"][1]["parts"][0], call_part)
        self.assertEqual(body["contents"][2]["parts"][0]["functionResponse"], {"name": "get_market", "response": {"title": "T"}})

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
        self.chat = AgentChat(workspace, governor)
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

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        headers = {"Host": "localhost", "Content-Type": "application/json", "X-CSRF-Token": self.server.CSRF,
                   "Cookie": f"governtrade={self.server.SESSION}"}
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

    def test_bad_chat_requests_are_400s(self):
        self.chat.connect = ScriptedModel()
        self.assertEqual(self.call("POST", "/api/action", {"action": "chat", "agent": "agent", "text": ""})[0], 400)
        self.assertEqual(self.call("POST", "/api/action", {"action": "chat", "agent": "nobody", "text": "hi"})[0], 400)


if __name__ == "__main__":
    unittest.main()
