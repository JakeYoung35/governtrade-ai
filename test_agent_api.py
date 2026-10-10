"""The user's own LLM connects with an agent API token: self-hosted agents, tokens, and the scoped governed API."""
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from governor_service import GovernorService
from test_governor_service import GovernedVenue, submission, workspace_state
from test_engine import FakeVault
from workspace import SELF_HOSTED, TOKEN_PREFIX, Workspace, agent_for_token, token_hash


class KeyVault(FakeVault):
    def __init__(self):
        self.deleted = []

    def put(self, value, reference=None):
        return reference or "MODEL-REF"

    def delete(self, reference):
        self.deleted.append(reference)


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.s = workspace_state()
        self.vault = KeyVault()
        self.workspace = Workspace(self.vault)
        self.workspace.venue = lambda account: GovernedVenue()

    def token(self, agent="agent", op="create"):
        return self.workspace.action(self.s, {"action": "agent-token", "id": agent, "op": op})

    def test_a_self_hosted_agent_needs_no_provider_key(self):
        with patch("workspace.validate_model_key", side_effect=AssertionError("must not call a provider")):
            result = self.workspace.action(self.s, {"action": "agent-save", "accountId": "account", "name": "My LLM",
                                                    "provider": SELF_HOSTED, "model": " Llama 3.1 70B "})
        agent = next(a for a in self.s["agentConfigs"] if a["id"] == result["agentId"])
        self.assertEqual((agent["provider"], agent["model"], agent["modelValidated"], agent["status"]),
                         (SELF_HOSTED, "Llama 3.1 70B", True, "draft"))
        self.assertNotIn("modelCredentialRef", agent)
        self.workspace.action(self.s, {"action": "mandate-save", "id": agent["id"], "personality": "Moderate",
                                       "mandate": {"autoLimit": 10, "dailyCap": 50, "minimum": 0, "newMarketApproval": False}})
        self.assertEqual(agent["status"], "active")
        with self.assertRaisesRegex(ValueError, "Name the model"):
            self.workspace.action(self.s, {"action": "agent-save", "accountId": "account", "name": "x", "provider": SELF_HOSTED, "model": " "})

    def test_switching_an_agent_to_self_hosted_deletes_its_provider_key(self):
        self.s["agentConfigs"][0]["modelCredentialRef"] = "OLD-REF"
        self.workspace.action(self.s, {"action": "agent-save", "id": "agent", "accountId": "account", "name": "agent",
                                       "provider": SELF_HOSTED, "model": "my model"})
        self.assertEqual(self.vault.deleted, ["OLD-REF"])
        self.assertNotIn("modelCredentialRef", self.s["agentConfigs"][0])

    def test_tokens_are_stored_hashed_shown_once_and_replaceable(self):
        first = self.token()["token"]
        self.assertTrue(first.startswith(TOKEN_PREFIX))
        stored = self.s["agentConfigs"][0]["apiToken"]
        self.assertEqual((stored["hash"], stored["last4"]), (token_hash(first), first[-4:]))
        self.assertNotIn(first, json.dumps(self.s))
        public = next(a for a in self.workspace.public(self.s)["agentConfigs"] if a["id"] == "agent")
        self.assertEqual(public["apiToken"], {"last4": first[-4:], "createdAt": stored["createdAt"]})
        self.assertEqual(agent_for_token(self.s, "Bearer " + first)["id"], "agent")
        second = self.token()["token"]
        self.assertIsNone(agent_for_token(self.s, "Bearer " + first))  # replaced
        self.assertEqual(agent_for_token(self.s, "Bearer " + second)["id"], "agent")
        self.token(op="revoke")
        self.assertIsNone(agent_for_token(self.s, "Bearer " + second))
        self.assertIsNone(next(a for a in self.workspace.public(self.s)["agentConfigs"] if a["id"] == "agent")["apiToken"])

    def test_only_valid_tokens_of_live_agents_match(self):
        token = self.token()["token"]
        for header in (None, "", token, "Bearer ", "Bearer gt_wrong", "Basic " + token, "bearer " + token):
            with self.subTest(header=header):
                self.assertIsNone(agent_for_token(self.s, header))
        self.workspace.action(self.s, {"action": "agent-delete", "id": "agent"})
        self.assertNotIn("apiToken", self.s["agentConfigs"][0])
        with self.assertRaisesRegex(ValueError, "Deleted agents"):
            self.token()
        with self.assertRaisesRegex(ValueError, "create or revoke"):
            self.token(agent="agent-2", op="rotate")


class ApiTests(unittest.TestCase):
    """The governed API on the real handler, called the way a user's own LLM would."""

    def setUp(self):
        import server
        self.server = server
        self.tmp = tempfile.TemporaryDirectory()
        self.venue = GovernedVenue()
        workspace = Workspace(KeyVault())
        workspace.venue = lambda account: self.venue
        s = workspace_state()
        for agent in s["agentConfigs"]:
            agent.update(provider=SELF_HOSTED, model="my model")
        s["chats"] = {"agent": [{"role": "customer", "text": "mine"}], "agent-2": [{"role": "customer", "text": "theirs"}]}
        self.token = workspace.action(s, {"action": "agent-token", "id": "agent", "op": "create"})["token"]
        self.other = workspace.action(s, {"action": "agent-token", "id": "agent-2", "op": "create"})["token"]
        Path(self.tmp.name, "main.json").write_text(json.dumps(s))
        patches = [patch.object(server, "DATA", Path(self.tmp.name)), patch.object(server, "STORE", None),
                   patch.object(server, "WORKSPACE", workspace), patch.object(server, "GOVERNOR", GovernorService(workspace)),
                   patch.dict(os.environ, {"GOVERNTRADE_AGENT_TOKEN": "operator-token"})]
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
        headers = {"Host": "localhost", "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        conn.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
        response = conn.getresponse()
        data = json.loads(response.read() or b"{}")
        conn.close()
        return response.status, data

    def test_the_llm_reads_the_format_and_who_it_is(self):
        code, schema = self.call("GET", "/api/governor/schema", token=self.token)
        self.assertEqual((code, schema["jsonSchema"]["title"]), (200, "GovernedProposal"))
        self.assertEqual((schema["agentId"], schema["example"]["agentId"]), ("agent", "agent"))  # the token's agent
        self.assertNotIn("agentId", self.call("GET", "/api/governor/schema", token="operator-token")[1])
        code, me = self.call("GET", "/api/governor/agent", token=self.token)
        self.assertEqual((code, me["agentId"], me["canPropose"], me["venue"]["environment"]), (200, "agent", True, "sandbox"))
        self.assertNotIn("apiToken", json.dumps(me))
        self.assertEqual(self.call("GET", "/api/governor/agent", token="operator-token")[0], 400)

    def test_bad_tokens_are_refused(self):
        for token in (None, "gt_not-a-real-token", "wrong"):
            with self.subTest(token=token):
                self.assertEqual(self.call("GET", "/api/governor/schema", token=token)[0], 403)
                self.assertEqual(self.call("POST", "/api/governor/proposals", submission(), token=token)[0], 403)

    def test_a_proposal_from_the_llm_is_processed_by_the_governor(self):
        payload = {k: v for k, v in submission().items() if k != "agentId"}  # the token implies the agent
        code, body = self.call("POST", "/api/governor/proposals", payload, token=self.token)
        self.assertEqual((code, body["status"]), (202, "queued"))
        self.assertTrue(self.server.governor_pass(score=False))  # one governor batch
        code, view = self.call("GET", f"/api/governor/proposals/{body['governorProposalId']}", token=self.token)
        self.assertEqual((code, view["agentId"], view["status"], view["decision"]["decision"]), (200, "agent", "submitted", "approved"))
        self.assertEqual(view["order"]["status"], "filled")

    def test_a_token_acts_only_for_its_own_agent(self):
        self.assertEqual(self.call("POST", "/api/governor/proposals", submission(agentId="agent-2"), token=self.token)[0], 403)
        self.assertEqual(self.call("POST", "/api/governor/proposals", submission(), token=self.other)[0], 403)
        code, theirs = self.call("POST", "/api/governor/proposals", submission(agentId="agent-2"), token=self.other)
        self.assertEqual(code, 202)
        self.assertEqual(self.call("GET", f"/api/governor/proposals/{theirs['governorProposalId']}", token=self.token)[0], 404)
        self.assertEqual(self.call("GET", f"/api/governor/proposals/{theirs['governorProposalId']}", token="operator-token")[0], 200)
        code, worker = self.call("GET", "/api/worker", token=self.token)
        self.assertEqual((code, [a["id"] for a in worker["agents"]], list(worker["instructions"])), (200, ["agent"], ["agent"]))
        self.assertEqual(len(self.call("GET", "/api/worker", token="operator-token")[1]["agents"]), 2)

    def test_the_shared_worker_token_still_works(self):
        code, body = self.call("POST", "/api/governor/proposals", submission(), token="operator-token")
        self.assertEqual((code, body["status"]), (202, "queued"))

    def test_hosted_instances_let_agent_tokens_through_the_password_gate(self):
        with patch.dict(os.environ, {"RENDER": "true", "GOVERNTRADE_ACCESS_PASSWORD": "pw",
                                     "GOVERNTRADE_ALLOWED_HOSTS": "localhost"}):
            self.assertEqual(self.call("GET", "/api/governor/agent", token=self.token)[0], 200)
            self.assertEqual(self.call("GET", "/api/state")[0], 401)  # the UI still needs the password
            self.assertEqual(self.call("GET", "/api/state", token=self.token)[0], 401)


if __name__ == "__main__":
    unittest.main()
