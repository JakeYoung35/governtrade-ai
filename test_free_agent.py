"""The GovernTrade free agent: a Gemini agent on the server's key, with a per-workspace daily message limit."""
import datetime as dt
import os
import unittest
from unittest.mock import patch

import providers
from agent_chat import AgentChat
from agent_llm import ModelTurn
from test_agent_chat import ChatTest, ScriptedModel, calls, propose
from workspace import Workspace

FREE_ENV = {'GOVERNTRADE_FREE_AGENT_GEMINI_KEY': 'server-gemini-key', 'GOVERNTRADE_FREE_AGENT_DAILY_MESSAGES': '3'}


class CatalogTests(unittest.TestCase):
    def test_offered_only_when_the_server_has_a_key(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertNotIn(providers.FREE_PROVIDER, providers.catalog())
        with patch.dict(os.environ, FREE_ENV, clear=True):
            free = providers.catalog()[providers.FREE_PROVIDER]
            self.assertEqual((free['models'], free['free'], free['dailyMessages']), (['gemini-3.1-flash-lite'], True, 3))
            self.assertEqual(free['shortLabel'], 'GovernTrade free agent')  # fits on agent cards
            self.assertIn('improve its products', free['notice'])
            self.assertNotIn('server-gemini-key', str(providers.catalog()))  # the key never reaches the browser
        with patch.dict(os.environ, {**FREE_ENV, 'GOVERNTRADE_FREE_AGENT_MODEL': 'gemini-3.5-flash'}, clear=True):
            self.assertEqual(providers.catalog()[providers.FREE_PROVIDER]['models'], ['gemini-3.5-flash'])

    def test_the_free_provider_cannot_be_used_with_a_user_key(self):
        with patch.dict(os.environ, FREE_ENV, clear=True), self.assertRaises(ValueError):
            providers.validate_model_key(providers.FREE_PROVIDER, 'gemini-3.1-flash-lite', 'user-key')


class FreeAgentTests(ChatTest):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, FREE_ENV)
        env.start();self.addCleanup(env.stop)
        self.checked = []
        remote = patch.object(providers, '_check_remote', side_effect=lambda *a: self.checked.append(a))
        remote.start();self.addCleanup(remote.stop)
        self.deleted = []
        self.workspace.vault.put = lambda value, reference=None: reference or 'NEW-REF'
        self.workspace.vault.delete = lambda reference: self.deleted.append(reference)

    def save_free(self, agent_id=None, name='Free agent'):
        return self.workspace.action(self.s, {'action': 'agent-save', 'id': agent_id, 'name': name,
                                              'provider': providers.FREE_PROVIDER, 'model': 'gemini-3.1-flash-lite',
                                              'accountId': self.s['accounts'][0]['id']})

    def test_saving_a_free_agent_needs_no_key_and_stores_none(self):
        agent_id = self.save_free()['agentId']
        agent = next(a for a in self.s['agentConfigs'] if a['id'] == agent_id)
        self.assertEqual((agent['provider'], agent['model'], agent['modelValidated']), (providers.FREE_PROVIDER, 'gemini-3.1-flash-lite', True))
        self.assertNotIn('modelCredentialRef', agent);self.assertIsNone(agent['modelKeyMask'])
        self.assertEqual(self.checked, [('google', 'gemini-3.1-flash-lite', 'server-gemini-key')])
        public = next(a for a in self.workspace.public(self.s)['agentConfigs'] if a['id'] == agent_id)
        self.assertNotIn('server-gemini-key', str(public))

    def test_switching_an_agent_to_the_free_agent_deletes_its_own_key(self):
        self.save_free('agent')
        self.assertEqual(self.deleted, ['MODEL-REF'])
        self.assertNotIn('modelCredentialRef', self.s['agentConfigs'][0])

    def test_unavailable_or_wrong_model_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'free agent'):
            self.workspace.action(self.s, {'action': 'agent-save', 'name': 'x', 'provider': providers.FREE_PROVIDER,
                                           'model': 'gemini-3.5-flash', 'accountId': self.s['accounts'][0]['id']})
        with patch.dict(os.environ, {'GOVERNTRADE_FREE_AGENT_GEMINI_KEY': ''}), self.assertRaisesRegex(ValueError, 'not available'):
            self.save_free()

    def test_deleting_a_free_agent_works_without_a_stored_key(self):
        agent_id = self.save_free()['agentId']
        self.workspace.action(self.s, {'action': 'agent-delete', 'id': agent_id})
        self.assertEqual(next(a for a in self.s['agentConfigs'] if a['id'] == agent_id)['status'], 'deleted')

    def test_chat_uses_the_server_key_through_gemini_and_still_goes_through_the_governor(self):
        self.save_free('agent')
        model = ScriptedModel(calls(propose()), ModelTurn('I proposed $20 on Cleveland.'))
        _, summary = self.chat(model)
        self.assertEqual((model.opened['provider'], model.opened['model'], model.opened['key']),
                         ('google', 'gemini-3.1-flash-lite', 'server-gemini-key'))
        self.assertEqual(summary['proposals'][0]['code'], 202)  # queued for the governor like any agent's proposal
        self.assertEqual(self.s['freeAgentUsage']['messages'], 1)

    def test_the_daily_limit_is_per_workspace_and_resets_at_midnight_utc(self):
        self.save_free('agent')
        day = dt.datetime(2026, 10, 11, 9, tzinfo=dt.timezone.utc)
        service = AgentChat(self.workspace, self.governor, connect=ScriptedModel(*[ModelTurn('ok')] * 5), clock=lambda: day)
        for _ in range(3):
            job = service.begin(self.s, {'agent': 'agent', 'text': 'What can I bet on?'})
            service.finish(self.s, job, service.run(job))
        with self.assertRaisesRegex(ValueError, '3 messages for today are used up'):
            service.begin(self.s, {'agent': 'agent', 'text': 'One more?'})
        self.assertEqual(self.s['chats']['agent'][-1]['role'], 'agent')  # the refused message was not recorded
        day = dt.datetime(2026, 10, 12, 0, 1, tzinfo=dt.timezone.utc)
        job = service.begin(self.s, {'agent': 'agent', 'text': 'New day'})
        service.finish(self.s, job, service.run(job))
        self.assertEqual(self.s['freeAgentUsage'], {'day': '2026-10-12', 'messages': 1})

    def test_agents_with_their_own_key_are_not_limited(self):
        with patch.dict(os.environ, {'GOVERNTRADE_FREE_AGENT_DAILY_MESSAGES': '0'}):
            model = ScriptedModel(ModelTurn('ok'))
            self.chat(model, text='hello')
        self.assertEqual(model.opened['key'], 'sk-test-secret')
        self.assertNotIn('freeAgentUsage', self.s)

    def test_chat_explains_when_the_server_key_is_removed(self):
        self.save_free('agent')
        with patch.dict(os.environ, {'GOVERNTRADE_FREE_AGENT_GEMINI_KEY': ''}):
            with self.assertRaisesRegex(ValueError, 'your own API key'):
                AgentChat(self.workspace, self.governor, connect=ScriptedModel()).begin(self.s, {'agent': 'agent', 'text': 'hi'})


if __name__ == '__main__':
    unittest.main()
