"""An agent's look: the seed and market focus its creature is drawn from. Cosmetic, validated, and kept on edit."""
import os
import unittest
from unittest.mock import patch

import providers
from tests.python.test_agent_chat import ChatTest

FREE_ENV = {'GOVERNTRADE_FREE_AGENT_GEMINI_KEY': 'server-gemini-key', 'GOVERNTRADE_FREE_AGENT_DAILY_MESSAGES': '3'}


class AgentLookTests(ChatTest):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, FREE_ENV)
        env.start();self.addCleanup(env.stop)
        remote = patch.object(providers, '_check_remote', return_value=None)
        remote.start();self.addCleanup(remote.stop)

    def save(self, agent_id=None, **extra):
        return self.workspace.action(self.s, {'action': 'agent-save', 'id': agent_id, 'name': 'Rally',
                                              'provider': providers.FREE_PROVIDER, 'model': 'gemini-3.1-flash-lite',
                                              'accountId': self.s['accounts'][0]['id'], **extra})

    def agent(self, agent_id):
        return next(a for a in self.s['agentConfigs'] if a['id'] == agent_id)

    def test_a_new_agent_keeps_its_look_and_the_browser_sees_it(self):
        agent_id = self.save(look={'seed': 'k3v9qa', 'focus': 'sports'})['agentId']
        self.assertEqual(self.agent(agent_id)['look'], {'seed': 'k3v9qa', 'focus': 'sports'})
        public = next(a for a in self.workspace.public(self.s)['agentConfigs'] if a['id'] == agent_id)
        self.assertEqual(public['look'], {'seed': 'k3v9qa', 'focus': 'sports'})

    def test_a_bad_look_is_refused_before_anything_is_stored(self):
        before = len(self.s['agentConfigs'])
        for look in ({'seed': 'BAD!', 'focus': 'sports'}, {'seed': 'k3v9qa', 'focus': 'weather'},
                     {'seed': 'k3v9qa'}, {'seed': 'k3v9qa', 'focus': 'sports', 'color': 'red'}, 'k3v9qa',
                     {'seed': 'abc', 'focus': 'sports'}, {'seed': 'a' * 13, 'focus': 'sports'}):
            with self.subTest(look=look), self.assertRaises(ValueError):
                self.save(look=look)
        self.assertEqual(len(self.s['agentConfigs']), before)

    def test_editing_without_a_look_keeps_the_saved_one(self):
        agent_id = self.save(look={'seed': 'k3v9qa', 'focus': 'sports'})['agentId']
        self.save(agent_id, name='Rally 2')
        self.assertEqual(self.agent(agent_id)['look'], {'seed': 'k3v9qa', 'focus': 'sports'})
        self.assertEqual(self.agent(agent_id)['name'], 'Rally 2')

    def test_an_agent_saved_without_a_look_has_none(self):
        agent_id = self.save()['agentId']
        self.assertNotIn('look', self.agent(agent_id))

    def test_rerolling_a_look(self):
        agent_id = self.save(look={'seed': 'k3v9qa', 'focus': 'sports'})['agentId']
        self.workspace.action(self.s, {'action': 'agent-look', 'id': agent_id, 'look': {'seed': 'zz9y8x7w', 'focus': 'crypto'}})
        self.assertEqual(self.agent(agent_id)['look'], {'seed': 'zz9y8x7w', 'focus': 'crypto'})
        with self.assertRaises(ValueError):
            self.workspace.action(self.s, {'action': 'agent-look', 'id': agent_id, 'look': {'seed': 'no', 'focus': 'crypto'}})
        self.assertEqual(self.agent(agent_id)['look']['seed'], 'zz9y8x7w')

    def test_a_deleted_agent_cannot_change_its_look(self):
        agent_id = self.save(look={'seed': 'k3v9qa', 'focus': 'sports'})['agentId']
        self.workspace.action(self.s, {'action': 'agent-delete', 'id': agent_id})
        with self.assertRaises(ValueError):
            self.workspace.action(self.s, {'action': 'agent-look', 'id': agent_id, 'look': {'seed': 'zz9y8x7w', 'focus': 'crypto'}})


if __name__ == '__main__':
    unittest.main()
