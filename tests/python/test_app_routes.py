"""The front end routes in the browser, so the server answers its page paths with the app shell."""
import http.client
import os
import threading
import unittest
from unittest.mock import patch

import server


class AppRouteTests(unittest.TestCase):
    def setUp(self):
        p = patch.dict(os.environ, {}, clear=True);p.start();self.addCleanup(p.stop)
        self.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close);self.addCleanup(self.httpd.shutdown)
        with open(server.ROOT / 'static' / 'index.html', 'rb') as f:self.shell = f.read()

    def get(self, path):
        port = self.httpd.server_address[1]
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        conn.request('GET', path, headers={'Host': f'localhost:{port}'})
        r = conn.getresponse();body = r.read();conn.close()
        return r.status, r.getheader('Content-Type', ''), body

    def head(self, path):
        port = self.httpd.server_address[1]
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        conn.request('HEAD', path, headers={'Host': f'localhost:{port}'})
        r = conn.getresponse();body = r.read();conn.close()
        return r.status, r.getheader('Content-Type', ''), body

    def test_page_paths_get_the_app_shell(self):
        for path in ('/', '/agents', '/agents/3f2a', '/rules?from=setup', '/activity/', '/vendor', '/fonts/'):
            with self.subTest(path=path):
                status, kind, body = self.get(path)
                self.assertEqual(status, 200)
                self.assertTrue(kind.startswith('text/html'))
                self.assertEqual(body, self.shell)

    def test_files_are_still_served_as_files(self):
        status, kind, body = self.get('/vendor/preact.module.js')
        self.assertEqual(status, 200);self.assertIn('javascript', kind);self.assertNotEqual(body, self.shell)
        self.assertEqual(self.get('/js/missing.js')[0], 404)

    def test_head_matches_get_for_page_paths(self):
        # Link checkers and prefetchers send HEAD; it must not 404 where GET returns the shell.
        status, kind, body = self.head('/agents')
        self.assertEqual(status, 200)
        self.assertTrue(kind.startswith('text/html'))
        self.assertEqual(body, b'')

    def test_head_still_serves_files_and_404s_missing_ones(self):
        self.assertEqual(self.head('/js/main.js')[0], 200)
        self.assertEqual(self.head('/nope.txt')[0], 404)

    def test_api_paths_never_fall_back_to_the_shell(self):
        status, _, body = self.get('/api/unknown')
        self.assertEqual(status, 404);self.assertNotEqual(body, self.shell)

    def test_route_rule(self):
        self.assertTrue(server.app_route('/agents/abc?x=1'))
        for path in ('/', '/api/state', '/healthz', '/js/main.js', '/fonts/Geist-Variable.woff2'):
            with self.subTest(path=path):self.assertFalse(server.app_route(path))


if __name__ == '__main__':
    unittest.main()
