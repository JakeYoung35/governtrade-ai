"""Exercise the real HTTP handler without a listening socket or Google network calls."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import server
import tenancy
from agent_llm import ModelTurn
from tests.python.test_agent_chat import ChatVenue, KeyVault, ScriptedModel, calls, propose
from tests.python.test_governor_service import workspace_state


class Socket:
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = bytearray()
    def makefile(self, *args, **kwargs):
        return self.input
    def sendall(self, data):
        self.output.extend(data)


class TenantChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.identities = {c: {'id': c * 64, 'csrf': 'csrf-' + c} for c in ('a', 'b')}
        auth = Mock()
        auth.session.side_effect = lambda token: self.identities.get(token)
        for context in (patch.object(server, 'DATA', self.root), patch.object(server, 'STORE', None),
                        patch.object(server.authentication, 'enabled', return_value=True),
                        patch.object(server, 'auth_store', return_value=auth),
                        patch.object(server, 'multiuser_ready'), patch.object(server.hosting, 'hosted', return_value=False)):
            context.start()
            self.addCleanup(context.stop)
        self.tenants = {}
        for c, identity in self.identities.items():
            with tenancy.activate(self.root, identity['id'], lambda s: server.save('main', s)) as tenant:
                self.tenants[c] = tenant
                tenant.workspace.vault = Mock(get=lambda ref, c=c: {'api_key': 'key-' + c})
                venue = ChatVenue()
                tenant.workspace.venue = lambda account, venue=venue: venue
                tenant.chat.market_data = lambda account, venue=venue: venue
                state = workspace_state()
                for agent in state['agentConfigs']:
                    agent.update(provider='anthropic', model='test', modelCredentialRef='same-ref')
                tenant.workspace.refresh(state, state['accounts'][0])
                server.save('main', state)

    def call(self, user, path, body=None):
        data = json.dumps(body).encode() if body else b''
        method = 'POST' if body else 'GET'
        headers = f'{method} {path} HTTP/1.0\r\nHost: localhost\r\nCookie: governtrade-session={user}\r\nX-CSRF-Token: csrf-{user}\r\nContent-Length: {len(data)}\r\n\r\n'
        sock = Socket(headers.encode() + data)
        with patch.object(server.Handler, 'log_message'):
            server.Handler(sock, ('127.0.0.1', 12345), Mock())
        head, body = bytes(sock.output).split(b'\r\n\r\n', 1)
        return int(head.split()[1]), json.loads(body)

    def test_chat_phases_polling_and_busy_slots_are_per_user(self):
        test = self
        b_model = ScriptedModel(calls(propose()), ModelTurn('B reply'))
        self.tenants['b'].chat.connect = b_model
        nested = []
        class ConcurrentModel(ScriptedModel):
            def step(self):
                if not nested:
                    test.assertIs(tenancy.CURRENT.get(), test.tenants['a'])
                    nested.append(test.call('b', '/api/action', {'action': 'chat', 'agent': 'agent', 'text': 'Put $20 on Cleveland'}))
                    test.assertIs(tenancy.CURRENT.get(), test.tenants['a'])
                return super().step()
        a_model = ConcurrentModel(calls(propose()), ModelTurn('A reply'))
        self.tenants['a'].chat.connect = a_model
        code, body = self.call('a', '/api/action', {'action': 'chat', 'agent': 'agent', 'text': 'Put $20 on Cleveland'})
        self.assertEqual((code, nested[0][0]), (200, 200))
        self.assertEqual((a_model.opened['key'], b_model.opened['key']), ('key-a', 'key-b'))
        self.assertEqual(body['csrf'], 'csrf-a')
        self.assertEqual(body['state']['chats']['agent'][-1]['text'], 'A reply')
        self.assertEqual(nested[0][1]['state']['chats']['agent'][-1]['text'], 'B reply')
        # Clearing only B's requests must not affect A's poll.
        with tenancy.activate(self.root, self.identities['b']['id'], lambda s: server.save('main', s)):
            state = server.read('main');state['governorRequests'] = [];server.save('main', state)
        self.assertEqual(self.call('b', '/api/chat/proposals'), (200, {'chatProposals': {}}))
        self.assertEqual(len(self.call('a', '/api/chat/proposals')[1]['chatProposals']['agent']), 1)
        self.assertFalse((self.root / 'main.json').exists())
        self.assertFalse(self.tenants['a'].chat._busy)
        self.assertFalse(self.tenants['b'].chat._busy)
        self.assertIsNone(tenancy.CURRENT.get())

    def test_poll_requires_sign_in(self):
        self.assertEqual(self.call('unknown', '/api/chat/proposals')[0], 401)


if __name__ == '__main__':
    unittest.main()
