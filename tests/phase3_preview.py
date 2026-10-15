"""Isolated UI fixture for the redesign (Phase 3 onward). Run: python3 -m tests.phase3_preview (PORT, default 3123).
No credentials, persistence, external models or venue calls. Not a production server.
Chat uses a scripted model: it proposes the dollar amount in the message; "[slow]" and "[fail]" exercise waiting and
errors, "[drop]" stores the message but closes the connection unanswered, and "[expire]" answers 401.
"""
import copy
import datetime as dt
import json
import os
import re
import time
from unittest.mock import patch
from tests.python.test_agent_chat import ChatVenue, ScriptedModel, calls, propose
from agent_chat import AgentChat
from agent_llm import ModelTurn
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from tests.python.test_governor_service import ServiceTest

case = ServiceTest(); case.setUp()
case.venue = ChatVenue()
case.workspace.venue = lambda account: case.venue
class FixtureVault:
    def get(self, reference): return {'api_key': 'fixture'}
    def put(self, value, reference=None): return 'fixture'
    def delete(self, reference): pass
case.workspace.vault = FixtureVault()
patch('workspace.validate_model_key', lambda *args: None).start()
patch('providers.free_agent_key', lambda: 'fixture-free-key').start()  # offers the free agent; chat stays scripted
patch('workspace.validate_free_agent', lambda *args: None).start()  # no call to Gemini with the placeholder key
patch('agent_chat.public_market_data', lambda account: case.venue).start()
class FixtureKeys:
    """Saving Kalshi keys signs one request to check the key; here nothing is signed or sent."""
    base = ''
    def headers(self, method, url): return {}
patch('workspace.adapter', lambda *args: FixtureKeys()).start()
for agent in case.s['agentConfigs']:
    agent.update(provider='anthropic', model='fixture-model', modelCredentialRef='fixture', modelKeyMask='sk-a…3f9c')
case.submit(maxBudgetUsd=100, estimatedYesProbability=0.8)
case.service.run_batch(case.s)
s = case.s
original = copy.deepcopy(s['orders'][0])
original['market']['name'] = 'Cleveland wins against Baltimore'
original['id'] = 'approve-example'
original['governedTerms']['approveBy'] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat()
s['agentConfigs'][0].update(name='Rally', look={'seed': 'k3v9qa', 'focus': 'sports'})
s['agentConfigs'][1].update(name='Scout', look={'seed': 'abc12345', 'focus': 'politics'})
s['orders'] = [original, {**copy.deepcopy(original), 'id': 'deny-example'},
               {**copy.deepcopy(original), 'id': 'expire-example', 'governedTerms': {
                   **original['governedTerms'], 'approveBy': (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=90)).isoformat()}},
               {**copy.deepcopy(original), 'id': 'blocked-example', 'status': 'blocked', 'decision': 'BLOCK',
                'evaluations': [{'time': original['time'], 'outcome': 'BLOCK', 'layers': {'agent_mandate': 'BLOCK'},
                                  'checks': [{'name': 'Agent mandate', 'layer': 'agent_mandate', 'outcome': 'BLOCK', 'reason': 'Trade exceeds the agent daily limit.'}]}]}]
# Distinct visible fixture labels make UI interactions unambiguous.
for o, name in zip(s['orders'], ['Approve example', 'Deny example', 'Expiry example', 'Blocked example']):
    o['market']['name'] = name
def fixture_turns(text):
    """A stand-in model: proposes the dollar amount the user typed, like a real agent must.
    "[slow]" holds the reply for a few seconds (to see the thinking state); "[fail]" makes the model call fail."""
    if '[slow]' in text: time.sleep(4)
    if '[fail]' in text: return [RuntimeError('The fixture model is unavailable.')]
    amount = re.search(r'\$(\d+(?:\.\d+)?)', text)
    if not amount: return [ModelTurn('How much would you like to commit? Give me a dollar amount and I will propose it.')]
    return [calls(propose(maxBudgetUsd=float(amount.group(1)))), ModelTurn('I proposed the trade for governor review.')]
# Open positions and a saved key ID for the Accounts screen; set after the examples so the governor sized them first.
from tests.python.test_governor_service import MARKET
case.venue.positions = [{'ticker': MARKET, 'position_fp': '10.00', 'market_exposure_dollars': '5.00', 'realized_pnl_dollars': '0'},
                        {'ticker': 'KXBTCD-26OCT1517-T112999.99', 'position_fp': '-4.00', 'market_exposure_dollars': '2.12', 'realized_pnl_dollars': '0'}]
s['accounts'][0]['credentialMasks'] = {'key_id': 'a1b2…9f0e', 'private_key': 'saved'}
root = Path(__file__).resolve().parents[1] / 'static'
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(root), **kwargs)
    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache')  # like server.py: edited modules load on the next page view
        super().end_headers()
    def json(self, status, value):
        raw = json.dumps(value).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def state(self): return {'state': {**case.workspace.public(s), 'chatProposals': AgentChat(case.workspace,case.service).proposal_views(s)}, 'csrf': 'fixture'}
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/auth': return self.json(200, {'mode': 'fixture', 'user': None, 'csrf': 'fixture'})
        if path == '/api/state': return self.json(200, self.state())
        if '.' not in path.rsplit('/', 1)[-1]: self.path = '/'
        return super().do_GET()
    def do_POST(self):
        if self.path != '/api/action': return self.json(404, {'error': 'Fixture endpoint not found.'})
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if data.get('action') not in ('decision','agent-status','agent-look','mandate-save','agent-delete','agent-save','chat','rules-save','permissions','refresh',
                                      'account-save','account-test','account-disconnect','account-remove'): return self.json(400, {'error': 'This fixture does not support that action.'})
        try:
            result = None
            if data['action'] == 'chat':
                text = str(data.get('text', ''))
                if '[expire]' in text: return self.json(401, {'error': 'Sign in again.'})  # the session ran out
                chat = AgentChat(case.workspace, case.service, connect=ScriptedModel(*fixture_turns(text)))
                job = chat.begin(s, data)
                if job.get('duplicate'): result = {'duplicate': True, 'messageIndex': job['messageIndex']}  # like server.py
                else:
                    chat.finish(s, job, chat.run(job))
                    case.service.run_batch(s)
                    if '[drop]' in text:  # stored and answered, but the response never reaches the browser
                        self.close_connection = True
                        return
            else:
                result = case.workspace.action(s, data)
            return self.json(200, {**self.state(), 'result': result})  # the same shape as server.py's response
        except Exception as e: return self.json(400, {'error': str(e)})
if __name__ == '__main__':
    print('Isolated Phase 3 fixture: http://127.0.0.1:3123', flush=True)
    ThreadingHTTPServer(('127.0.0.1',int(os.environ.get('PORT','3123'))), Handler).serve_forever()
