import concurrent.futures
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from google.auth import crypt, jwt

import authentication as auth
import server
import tenancy


class IdentityTests(unittest.TestCase):
    def test_actual_google_verifier_rejects_signature_audience_expiry_and_nonce(self):
        # Exercise Google's verifier with a generated test signing key, without network or real credentials.
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        cert = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        signer = crypt.RSASigner.from_string(pem, 'test-key')
        claims = {'iss':'https://accounts.google.com','aud':'client','sub':'123','iat':int(time.time()),
                  'exp':int(time.time()+600),'nonce':'browser-nonce','email_verified':True,'email':'owner@example.com'}
        with patch('google.oauth2.id_token._fetch_certs', return_value={'test-key':cert}):
            identity = auth.verify_identity(jwt.encode(signer, claims).decode(), 'client', 'browser-nonce')
            self.assertEqual(identity['id'], auth.digest('https://accounts.google.com:123'))
            for changes in ({'aud':'another-client'},{'iss':'https://evil.example'},{'exp':int(time.time()-600)},
                            {'nonce':'another-browser'},{'email_verified':False},{'sub':''}):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    auth.verify_identity(jwt.encode(signer, {**claims, **changes}).decode(), 'client', 'browser-nonce')
            forged = jwt.encode(crypt.RSASigner.from_string(
                rsa.generate_private_key(public_exponent=65537,key_size=2048).private_bytes(
                    serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()), 'test-key'), claims)
            with self.assertRaises(ValueError):auth.verify_identity(forged.decode(), 'client', 'browser-nonce')

    def test_challenge_replay_expiry_and_session_revocation(self):
        with tempfile.TemporaryDirectory() as root:
            store = auth.AuthStore(root)
            token, nonce = store.challenge()
            identity = {'id':'a'*64,'name':'A','email':'a@example.com'}
            with patch.object(auth, 'verify_identity', return_value=identity):
                session, user = store.login(token, nonce, 'test', 'client')
                self.assertEqual(store.session(session)['id'], user['id'])
                self.assertEqual(auth.AuthStore(root).session(session)['id'], user['id'])
                with self.assertRaises(ValueError):store.login(token, nonce, 'test', 'client')
                with patch.object(auth.time, 'time', return_value=time.time()+auth.SESSION_SECONDS+1):
                    self.assertIsNone(store.session(session))
                store.revoke(session)
                self.assertIsNone(store.session(session))
                token, nonce = store.challenge()
                with patch.object(auth.time, 'time', return_value=time.time()+auth.CHALLENGE_SECONDS+1):
                    with self.assertRaises(ValueError):store.login(token, nonce, 'test', 'client')


class PersonalHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = {'RENDER':'true','RENDER_EXTERNAL_HOSTNAME':'app.onrender.com',
               'GOVERNTRADE_GOOGLE_CLIENT_ID':'client','GOVERNTRADE_DATA_DIR':self.temp.name,
               'GOVERNTRADE_PERSISTENT_STORAGE':'true','GOVERNTRADE_MASTER_KEY':Fernet.generate_key().decode(),
               'GOVERNTRADE_AGENT_TOKEN':'legacy-worker'}
        for p in (patch.dict(os.environ, env, clear=True), patch.object(server,'DATA',Path(self.temp.name)),
                  patch.object(server,'STORE',None)):
            p.start();self.addCleanup(p.stop)
        self.httpd = server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=self.httpd.serve_forever,daemon=True).start()
        self.addCleanup(self.httpd.server_close);self.addCleanup(self.httpd.shutdown)

    def call(self, method, path, cookie='', csrf='', body=None, extra=None):
        conn = http.client.HTTPConnection('127.0.0.1',self.httpd.server_address[1],timeout=5)
        conn.request(method,path,json.dumps(body) if body is not None else None,
                     {'Host':'app.onrender.com','Origin':'https://app.onrender.com',
                      'Cookie':cookie,'X-CSRF-Token':csrf,'Content-Type':'application/json',**(extra or {})})
        r=conn.getresponse();raw=r.read();headers=dict(r.getheaders());conn.close()
        return r.status,headers,json.loads(raw) if raw and headers.get('Content-Type')=='application/json' else raw

    def login(self, subject):
        _,headers,config=self.call('GET','/api/auth')
        user={'id':auth.digest('google:'+subject),'name':subject,'email':subject+'@example.com'}
        with patch.object(auth,'verify_identity',return_value=user):
            status,headers,body=self.call('POST','/api/auth/login',headers['Set-Cookie'].split(';')[0],
                                         body={'nonce':config['nonce'],'credential':'test'})
        self.assertEqual(status,200)
        self.assertIn('HttpOnly',headers['Set-Cookie']);self.assertIn('Secure',headers['Set-Cookie'])
        self.assertTrue(headers['Set-Cookie'].startswith('__Host-'))
        return headers['Set-Cookie'].split(';')[0],body['csrf'],user['id']

    def test_public_signin_private_endpoints_and_legacy_worker_denied(self):
        self.assertEqual(self.call('GET','/')[0],200)
        self.assertEqual(self.call('HEAD','/')[0],200)
        self.assertEqual(self.call('GET','/healthz')[0],200)
        for method,path in [('GET','/api/state?main'),('HEAD','/api/state'),('POST','/api/action'),
                            ('GET','/api/worker'),('GET','/api/governor/summary')]:
            self.assertEqual(self.call(method,path)[0],401)
        self.assertEqual(self.call('GET','/api/worker',extra={'Authorization':'Bearer legacy-worker'})[0],401)
        cookie,csrf,_=self.login('alice')
        self.assertEqual(self.call('GET','/api/worker',cookie,extra={'Authorization':'Bearer legacy-worker'})[0],403)
        self.assertEqual(self.call('GET','/api/state',cookie,extra={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.call('POST','/api/action',cookie,'wrong',{'action':'permissions','values':{'dailyCap':1}})[0],403)
        self.assertEqual(self.call('POST','/api/auth/logout',cookie,csrf)[0],200)
        self.assertEqual(self.call('GET','/api/state',cookie)[0],401)
        self.assertEqual(self.call('POST','/api/action',cookie,csrf,{'action':'permissions','values':{'dailyCap':1}})[0],401)

    def test_two_users_isolate_state_vaults_demo_and_foreign_resource_mutations(self):
        alice,ac,aid=self.login('alice');bob,bc,bid=self.login('bob')
        for uid,secret in ((aid,'alice-secret'),(bid,'bob-secret')):
            with tenancy.activate(server.DATA,uid,lambda s:server.save('main',s)),server.locked('main'):
                state=server.read('main');state['chats']['private']=[{'role':'customer','text':secret}]
                state['accounts']=[{'id':'same-account-id','name':secret,'venue':'kalshi','environment':'demo',
                                    'status':'disconnected','stale':True,'ceiling':{}}]
                server.workspace().vault.put({'api_key':secret},'same-reference')
                server.save('main',state)
        # Knowledge of another user's resource ID is insufficient to mutate it.
        with tenancy.activate(server.DATA,aid,lambda s:server.save('main',s)),server.locked('main'):
            state=server.read('main')
            state['agentConfigs']=[{'id':'alice-only-agent','accountId':'same-account-id','name':'Alice agent',
                                    'provider':'openai','model':'test','status':'paused','mandate':None}]
            server.save('main',state)
        self.assertEqual(self.call('POST','/api/action',bob,bc,
                         {'action':'agent-status','id':'alice-only-agent','status':'paused'})[0],400)
        for cookie,secret,other in ((alice,'alice-secret','bob-secret'),(bob,'bob-secret','alice-secret')):
            status,_,body=self.call('GET','/api/state?main',cookie)
            self.assertEqual(status,200);self.assertIn(secret,json.dumps(body));self.assertNotIn(other,json.dumps(body))
        self.assertEqual(self.call('POST','/api/action',alice,ac,{'action':'permissions','values':{'dailyCap':42},'userId':bid})[0],200)
        self.assertEqual(self.call('GET','/api/state?main',alice)[2]['state']['permissions']['dailyCap'],42)
        self.assertNotEqual(self.call('GET','/api/state?main',bob)[2]['state']['permissions']['dailyCap'],42)
        self.assertEqual(self.call('POST','/api/action',bob,ac,{'action':'permissions','values':{'dailyCap':1}})[0],403)
        with tenancy.activate(server.DATA,aid,lambda s:server.save('main',s)):
            self.assertEqual(server.workspace().vault.get('same-reference')['api_key'],'alice-secret')
        with tenancy.activate(server.DATA,bid,lambda s:server.save('main',s)):
            self.assertEqual(server.workspace().vault.get('same-reference')['api_key'],'bob-secret')
        self.call('POST','/api/action',alice,ac,{'env':'demo','action':'reset'})
        self.assertFalse((server.DATA/'users'/bid/'demo.json').exists())
        self.assertFalse((server.DATA/'main.json').exists())
        self.assertIsNone(tenancy.CURRENT.get())

    def test_concurrent_users_and_governor_context_stay_separate(self):
        alice,ac,aid=self.login('alice');bob,bc,bid=self.login('bob')
        def set_cap(args):
            cookie,csrf,cap=args
            return self.call('POST','/api/action',cookie,csrf,{'action':'permissions','values':{'dailyCap':cap}})[0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(set_cap,[(alice,ac,11),(bob,bc,22)])),[200,200])
        for uid,expected in ((aid,11),(bid,22)):
            with tenancy.activate(server.DATA,uid,lambda s:server.save('main',s)):
                self.assertEqual(server.read('main')['permissions']['dailyCap'],expected)
                with patch.object(server.governor(),'plan',return_value={}) as plan:
                    self.assertFalse(server.governor_pass(False));plan.assert_called_once()
        self.assertIsNone(tenancy.CURRENT.get())

    def test_hosted_configuration_fails_closed(self):
        for name in ('GOVERNTRADE_MASTER_KEY','GOVERNTRADE_PERSISTENT_STORAGE','GOVERNTRADE_DATA_DIR'):
            with patch.dict(os.environ,{name:''}):self.assertEqual(self.call('GET','/')[0],503)
        with patch.object(server,'STORE',object()):self.assertEqual(self.call('GET','/')[0],503)
        self.assertEqual(self.call('POST','/api/auth/login',body={},extra={'Origin':'https://evil.example'})[0],403)


if __name__=='__main__':unittest.main()
