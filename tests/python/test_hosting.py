import base64
import http.client
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import hosting
import server


class HostingTests(unittest.TestCase):
    def test_bind_and_exact_hostname_allowlist(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(hosting.bind_host(), '127.0.0.1')
            self.assertTrue(hosting.allowed_host('localhost:3110'))
            self.assertFalse(hosting.allowed_host('evil.example'))
        with patch.dict(os.environ, {'RENDER':'true','RENDER_EXTERNAL_HOSTNAME':'app.onrender.com',
                                     'GOVERNTRADE_ALLOWED_HOSTS':'governtradeai.us,www.governtradeai.us'}, clear=True):
            self.assertEqual(hosting.bind_host(), '0.0.0.0')
            self.assertEqual(hosting.expected_origin('governtradeai.us'), 'https://governtradeai.us')
            for host in ('app.onrender.com','governtradeai.us','www.governtradeai.us:443'):
                self.assertTrue(hosting.allowed_host(host))
            for host in ('governtradeai.us.evil.example','evil@governtradeai.us','governtradeai.us/path',
                         'governtradeai.us:bad','governtradeai.us#fragment'):
                self.assertFalse(hosting.allowed_host(host))
            self.assertFalse(hosting.allowed_host('localhost:3110'))
            self.assertFalse(hosting.allowed_host('127.0.0.1'))


class HostedHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for p in (patch.dict(os.environ, {'RENDER':'true','RENDER_EXTERNAL_HOSTNAME':'app.onrender.com',
                                         'GOVERNTRADE_AGENT_TOKEN':'worker-token'}, clear=True),
                  patch.object(server, 'DATA', Path(self.temp.name)), patch.object(server, 'STORE', None)):
            p.start();self.addCleanup(p.stop)
        self.httpd = server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=self.httpd.serve_forever,daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def call(self, method, path, headers=None, body=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.httpd.server_address[1],timeout=5)
        conn.request(method,path,body=body,headers={'Host':'app.onrender.com',**(headers or {})})
        response=conn.getresponse();result=(response.status,dict(response.getheaders()),response.read())
        conn.close();return result

    def auth(self):
        return {'Authorization':'Basic '+base64.b64encode(b'governtrade:test-password').decode()}

    def test_unconfigured_hosted_workspace_is_locked_health_is_public(self):
        for method,path in [('GET','/'),('HEAD','/'),('GET','/api/state?main'),('POST','/api/action')]:
            status,headers,_=self.call(method,path)
            self.assertEqual(status,503)
            self.assertNotIn('Set-Cookie',headers)
        for method in ('GET','HEAD'):
            status,headers,body=self.call(method,'/healthz')
            self.assertEqual(status,200)
            self.assertNotIn('Set-Cookie',headers)
            if method=='HEAD':self.assertEqual(body,b'')
        self.assertEqual(self.call('GET','/healthz',{'Host':'evil.example'})[0],200)

    def cache_headers(self, path, headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.httpd.server_address[1],timeout=5)
        conn.request('GET',path,headers={'Host':'app.onrender.com',**(headers or {})})
        response=conn.getresponse();response.read();values=response.msg.get_all('Cache-Control') or []
        conn.close();return response.status,values

    def test_static_files_revalidate(self):
        with patch.dict(os.environ, {'GOVERNTRADE_ACCESS_PASSWORD':'test-password'}):
            for path in ('/js/main.js','/style.css'):
                self.assertEqual(self.cache_headers(path,self.auth()),(200,['no-cache']))

    def test_health_and_api_keep_single_no_store(self):
        with patch.dict(os.environ, {'GOVERNTRADE_ACCESS_PASSWORD':'test-password'}):
            self.assertEqual(self.cache_headers('/healthz'),(200,['no-store']))
            self.assertEqual(self.cache_headers('/api/state?demo',self.auth()),(200,['no-store']))

    def test_legacy_agent_proposals_retired_when_hosted(self):
        with patch.dict(os.environ, {'GOVERNTRADE_ACCESS_PASSWORD':'test-password'}):
            self.assertEqual(self.call('POST','/api/agent/proposals',{'Authorization':'Bearer worker-token'},'{}')[0],410)
            self.assertEqual(self.call('POST','/api/agent/proposals',self.auth(),'{}')[0],410)

    def test_password_gate_worker_scope_and_https_csrf(self):
        with patch.dict(os.environ, {'GOVERNTRADE_ACCESS_PASSWORD':'test-password'}):
            for method,path in [('GET','/'),('HEAD','/'),('GET','/api/state?demo'),('POST','/api/action')]:
                status,headers,_=self.call(method,path)
                self.assertEqual(status,401)
                self.assertIn('WWW-Authenticate',headers)
            self.assertEqual(self.call('GET','/',{'Authorization':'Basic !!!'})[0],401)
            self.assertEqual(self.call('GET','/',self.auth())[0],200)
            self.assertEqual(self.call('GET','/api/governor/schema',{'Authorization':'Bearer worker-token'})[0],200)
            self.assertEqual(self.call('GET','/api/state?demo',{'Authorization':'Bearer worker-token'})[0],401)
            status,headers,_=self.call('GET','/api/state?demo',self.auth())
            self.assertEqual(status,200)
            self.assertIn('; Secure',headers['Set-Cookie'])
            auth={**self.auth(),'Cookie':headers['Set-Cookie'].split(';')[0],
                  'X-CSRF-Token':server.CSRF,'Origin':'https://app.onrender.com','Content-Type':'application/json'}
            body='{"env":"demo","action":"reset"}'
            self.assertEqual(self.call('POST','/api/action',auth,body)[0],200)
            self.assertEqual(self.call('POST','/api/action',{**auth,'Origin':'https://evil.example'},body)[0],403)
            self.assertEqual(self.call('POST','/api/action',{**auth,'Origin':'http://app.onrender.com'},body)[0],403)
            self.assertEqual(self.call('POST','/api/action',{**auth,'X-CSRF-Token':'wrong'},body)[0],403)


if __name__=='__main__':unittest.main()
