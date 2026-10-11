"""Auth0 sign-in, the sign-in fixes (cookies, per-tab challenges, allowlist) and the personal-workspace fixes."""
import base64
import hashlib
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa

import authentication as auth
import hosting
import server
import tenancy

HOST = 'app.onrender.com'
ORIGIN = 'https://' + HOST


class CookieTests(unittest.TestCase):
    def test_an_unparseable_neighbour_cookie_does_not_hide_the_session(self):
        for header in ('governtrade-session=abc', 'theme=dark; governtrade-session=abc',
                       'a=b c; governtrade-session=abc', 'prefs={"x": 1}; governtrade-session=abc',
                       'governtrade-session-other=zzz; governtrade-session=abc'):
            with self.subTest(header=header):
                self.assertEqual(auth.cookie_value(header, 'governtrade-session'), 'abc')
        self.assertEqual(auth.cookie_value('', 'governtrade-session'), '')
        self.assertEqual(auth.cookie_value(None, 'governtrade-session'), '')


class ChallengeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store = auth.AuthStore(self.tmp.name)
        self.user = {'id': 'a' * 64, 'name': 'A', 'email': 'a@example.com'}

    def test_anonymous_challenges_write_nothing_and_cannot_evict_a_pending_sign_in(self):
        token, nonce = self.store.challenge()
        for _ in range(2000):
            self.store.challenge()
        with self.store.connection() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn('challenges', tables)
            self.assertEqual(db.execute('SELECT count(*) FROM used_nonces').fetchone()[0], 0)
        with patch.object(auth, 'verify_identity', return_value=self.user):
            session, _ = self.store.login(token, nonce, 'credential', 'client')
        self.assertEqual(self.store.session(session)['id'], self.user['id'])

    def test_tampered_or_mismatched_challenges_are_refused(self):
        token, nonce = self.store.challenge()
        other_token, other_nonce = self.store.challenge()
        with patch.object(auth, 'verify_identity', return_value=self.user):
            for bad_token, bad_nonce in ((token[:-2] + 'xx', nonce), (other_token, nonce), ('', nonce),
                                         (auth.AuthStore(tempfile.mkdtemp()).challenge()[0], nonce)):
                with self.subTest(token=bad_token[:12]), self.assertRaises(ValueError):
                    self.store.login(bad_token, bad_nonce, 'credential', 'client')

    def test_a_transient_provider_error_does_not_use_up_the_challenge(self):
        token, nonce = self.store.challenge()
        with patch.object(auth, 'verify_identity', side_effect=OSError('certificate fetch timed out')):
            with self.assertRaises(OSError):self.store.login(token, nonce, 'credential', 'client')
        with patch.object(auth, 'verify_identity', return_value=self.user):
            self.store.login(token, nonce, 'credential', 'client')  # the same challenge still works
            with self.assertRaises(ValueError):self.store.login(token, nonce, 'credential', 'client')  # but once

    def test_a_damaged_or_short_signing_key_is_refused_never_used(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'auth-secret').write_bytes(b'')  # e.g. a crash right after the file was created
            with self.assertRaisesRegex(ValueError, 'damaged'):auth.AuthStore(root)
        with patch.dict(os.environ, {'GOVERNTRADE_SESSION_SECRET': 'too-short'}):
            with self.assertRaisesRegex(ValueError, 'at least 32'):auth.AuthStore(tempfile.mkdtemp())
        with patch.dict(os.environ, {'GOVERNTRADE_SESSION_SECRET': 'x' * 40}):
            self.assertEqual(auth.AuthStore(tempfile.mkdtemp()).secret, b'x' * 40)

    def test_concurrent_first_starts_agree_on_one_key_and_leave_no_temporary_files(self):
        with tempfile.TemporaryDirectory() as root:
            winner = os.urandom(32)
            real_link = os.link

            def another_process_wins(source, target):
                Path(target).write_bytes(winner)  # published between our exists() check and our link
                return real_link(source, target)
            with patch.object(auth.os, 'link', side_effect=another_process_wins):
                self.assertEqual(auth.AuthStore(root).secret, winner)
            self.assertEqual(auth.AuthStore(root).secret, winner)
            self.assertEqual([p.name for p in Path(root).iterdir() if p.name.startswith('.auth-secret')], [])

    def test_the_signing_key_is_shared_by_every_store_on_the_same_disk(self):
        token, nonce = self.store.challenge()
        self.assertEqual(auth.AuthStore(self.tmp.name).unseal(token)['nonce'], nonce)
        self.assertEqual(os.stat(Path(self.tmp.name, 'auth-secret')).st_mode & 0o777, 0o600)


class AllowlistTests(unittest.TestCase):
    def test_addresses_domains_and_wildcard(self):
        with patch.dict(os.environ, {'GOVERNTRADE_ALLOWED_EMAILS': 'owner@example.com, @team.io'}):
            self.assertTrue(auth.email_allowed('owner@example.com'))
            self.assertTrue(auth.email_allowed('OWNER@example.com'))
            self.assertTrue(auth.email_allowed('dev@team.io'))
            self.assertFalse(auth.email_allowed('dev@evil-team.io'))
            self.assertFalse(auth.email_allowed('someone@example.com'))
            self.assertFalse(auth.email_allowed(''))
        for rule in ('', '*'):
            with patch.dict(os.environ, {'GOVERNTRADE_ALLOWED_EMAILS': rule}):
                self.assertTrue(auth.email_allowed('anyone@anywhere.com'))

    def test_a_removed_address_loses_its_sessions_and_its_governor_passes(self):
        with tempfile.TemporaryDirectory() as root:
            store = auth.AuthStore(root)
            token, nonce = store.challenge()
            user = {'id': 'b' * 64, 'name': 'B', 'email': 'b@example.com'}
            with patch.object(auth, 'verify_identity', return_value=user):
                session, _ = store.login(token, nonce, 'credential', 'client')
            self.assertIsNotNone(store.session(session));self.assertEqual(store.users(), ['b' * 64])
            with patch.dict(os.environ, {'GOVERNTRADE_ALLOWED_EMAILS': 'someone-else@example.com'}):
                self.assertIsNone(store.session(session))
                self.assertEqual(store.users(), [])
                token, nonce = store.challenge()
                with patch.object(auth, 'verify_identity', return_value=user), self.assertRaises(auth.NotAllowed):
                    store.login(token, nonce, 'credential', 'client')


class HostedServerTest(unittest.TestCase):
    """A hosted (Render) server on a temporary persistent disk."""
    provider_env = {'GOVERNTRADE_GOOGLE_CLIENT_ID': 'client'}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        env = {'RENDER': 'true', 'RENDER_EXTERNAL_HOSTNAME': HOST, 'GOVERNTRADE_DATA_DIR': self.tmp.name,
               'GOVERNTRADE_PERSISTENT_STORAGE': 'true', 'GOVERNTRADE_MASTER_KEY': Fernet.generate_key().decode(),
               'GOVERNTRADE_ALLOWED_EMAILS': 'owner@example.com', **self.provider_env}
        for p in (patch.dict(os.environ, env, clear=True), patch.object(server, 'DATA', Path(self.tmp.name)),
                  patch.object(server, 'STORE', None)):
            p.start();self.addCleanup(p.stop)
        self.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close);self.addCleanup(self.httpd.shutdown)

    def call(self, method, path, cookies=(), body=None, extra=None, origin=True):
        conn = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=5)
        headers = {'Host': HOST, 'Cookie': '; '.join(cookies), 'Content-Type': 'application/json', **(extra or {})}
        if origin:headers['Origin'] = ORIGIN
        conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        r = conn.getresponse();raw = r.read()
        cookies_set = [v for k, v in r.getheaders() if k.lower() == 'set-cookie']
        headers = dict(r.getheaders());conn.close()
        data = json.loads(raw) if raw and headers.get('Content-Type') == 'application/json' else raw
        return r.status, headers, cookies_set, data


class GoogleSignInTests(HostedServerTest):
    def challenge(self):
        status, _, cookies, config = self.call('GET', '/api/auth')
        self.assertEqual(status, 200);self.assertEqual(len(cookies), 1)
        return cookies[0].split(';')[0], config['nonce']

    def login(self, cookies, nonce, email='owner@example.com'):
        user = {'id': auth.digest('google:' + email), 'name': 'Owner', 'email': email}
        with patch.object(auth, 'verify_identity', return_value=user):
            return self.call('POST', '/api/auth/login', cookies, {'nonce': nonce, 'credential': 'test'})

    def test_two_tabs_each_keep_their_own_challenge(self):
        tab_a, nonce_a = self.challenge()
        tab_b, nonce_b = self.challenge()
        self.assertNotEqual(tab_a.split('=')[0], tab_b.split('=')[0])
        status, _, cookies, _ = self.login([tab_a, tab_b], nonce_a)  # the browser sends both cookies
        self.assertEqual(status, 200)
        session = next(c for c in cookies if c.startswith('__Host-governtrade-session=') and 'Max-Age=0' not in c)
        self.assertIn('Secure', session);self.assertIn('HttpOnly', session)
        self.assertEqual(self.login([tab_b], nonce_b)[0], 200)  # tab B still signs in

    def test_an_address_off_the_allowlist_is_refused_politely(self):
        cookie, nonce = self.challenge()
        status, _, _, body = self.login([cookie], nonce, 'stranger@example.com')
        self.assertEqual(status, 403);self.assertIn('allowed list', body['error'])

    def test_hosted_personal_workspaces_need_an_allowlist(self):
        with patch.dict(os.environ, {'GOVERNTRADE_ALLOWED_EMAILS': ''}):
            self.assertEqual(self.call('GET', '/api/auth')[0], 503)
        with patch.dict(os.environ, {'GOVERNTRADE_ALLOWED_EMAILS': '*'}):
            self.assertEqual(self.call('GET', '/api/auth')[0], 200)

    def test_assets_are_served_without_a_session_lookup(self):
        with patch.object(auth.AuthStore, 'session', return_value=None) as lookup:
            self.assertEqual(self.call('GET', '/app.js')[0], 200)
            self.assertEqual(self.call('HEAD', '/')[0], 200)
            self.assertEqual(lookup.call_count, 0)
            self.assertEqual(self.call('GET', '/api/state?main')[0], 401)
            self.assertEqual(lookup.call_count, 1)


class Auth0Tests(HostedServerTest):
    provider_env = {'AUTH0_DOMAIN': 'tenant.example.auth0.com', 'AUTH0_CLIENT_ID': 'client-123',
                    'AUTH0_CLIENT_SECRET': 'client-secret'}
    ISSUER = 'https://tenant.example.auth0.com/'

    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def start(self):
        status, headers, cookies, _ = self.call('GET', '/api/auth/start', origin=False)
        self.assertEqual(status, 303)
        location = urlsplit(headers['Location']);query = {k: v[0] for k, v in parse_qs(location.query).items()}
        self.assertEqual(location.netloc, 'tenant.example.auth0.com');self.assertEqual(location.path, '/authorize')
        self.assertEqual((query['response_type'], query['client_id'], query['code_challenge_method']),
                         ('code', 'client-123', 'S256'))
        self.assertEqual(query['redirect_uri'], ORIGIN + '/api/auth/callback')
        self.assertIn('SameSite=Lax', cookies[0]);self.assertIn('Secure', cookies[0])
        return cookies[0].split(';')[0], query

    def id_token(self, flow_nonce, **changes):
        now = int(time.time())
        claims = {'iss': self.ISSUER, 'aud': 'client-123', 'sub': 'google-oauth2|42', 'iat': now, 'exp': now + 600,
                  'nonce': flow_nonce, 'email': 'owner@example.com', 'email_verified': True, 'name': 'Owner', **changes}
        return jwt.encode({k: v for k, v in claims.items() if v is not None}, self.key, algorithm='RS256')

    def callback(self, cookie, query, token, code='auth-code', state=None):
        exchanged = []

        def exchange(code_, verifier, redirect_uri):
            exchanged.append((code_, verifier, redirect_uri));return token
        with patch.object(auth, 'auth0_exchange', side_effect=exchange), \
                patch.object(auth, 'auth0_signing_key', return_value=self.key.public_key()):
            status, headers, cookies, _ = self.call(
                'GET', f"/api/auth/callback?code={code}&state={state or query['state']}", [cookie], origin=False)
        return status, headers['Location'], cookies, exchanged

    def test_full_sign_in_with_pkce_then_logout(self):
        self.assertEqual(self.call('GET', '/api/auth')[3], {'mode': 'auth0', 'loginUrl': '/api/auth/start', 'user': None})
        cookie, query = self.start()
        status, location, cookies, exchanged = self.callback(cookie, query, self.id_token(query['nonce']))
        self.assertEqual((status, location), (303, '/'))
        code, verifier, redirect_uri = exchanged[0]
        self.assertEqual((code, redirect_uri), ('auth-code', ORIGIN + '/api/auth/callback'))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        self.assertEqual(challenge, query['code_challenge'])  # the verifier matches what Auth0 was promised
        session = next(c.split(';')[0] for c in cookies if c.startswith('__Host-governtrade-session=') and 'Max-Age=0' not in c)
        status, _, _, config = self.call('GET', '/api/auth', [session])
        self.assertEqual((status, config['mode'], config['user']['email']), (200, 'auth0', 'owner@example.com'))
        self.assertEqual(self.call('GET', '/api/state?main', [session])[0], 200)
        status, _, _, body = self.call('POST', '/api/auth/logout', [session], extra={'X-CSRF-Token': config['csrf']})
        self.assertEqual(status, 200)
        self.assertTrue(body['logoutUrl'].startswith('https://tenant.example.auth0.com/v2/logout?'))
        self.assertIn('client_id=client-123', body['logoutUrl'])
        self.assertEqual(self.call('GET', '/api/state?main', [session])[0], 401)

    def test_a_forged_or_mismatched_callback_never_creates_a_session(self):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cases = {'wrong nonce': {'nonce': 'another'}, 'wrong audience': {'aud': 'other-client'},
                 'wrong issuer': {'iss': 'https://evil.example/'}, 'expired': {'exp': int(time.time()) - 10},
                 'unverified email': {'email_verified': False}, 'no subject': {'sub': None}}
        for name, changes in cases.items():
            with self.subTest(name):
                cookie, query = self.start()
                status, location, cookies, _ = self.callback(cookie, query, self.id_token(query['nonce'], **changes))
                self.assertEqual((status, location), (303, '/?signin=failed'))
                self.assertFalse(any(c.startswith('__Host-governtrade-session=') and 'Max-Age=0' not in c for c in cookies))
        cookie, query = self.start()
        forged = jwt.encode({'iss': self.ISSUER, 'aud': 'client-123', 'sub': 'x', 'iat': int(time.time()),
                             'exp': int(time.time()) + 600, 'nonce': query['nonce'], 'email_verified': True,
                             'email': 'owner@example.com'}, other, algorithm='RS256')
        self.assertEqual(self.callback(cookie, query, forged)[1], '/?signin=failed')
        cookie, query = self.start()
        self.assertEqual(self.callback(cookie, query, self.id_token(query['nonce']), state='tampered')[1], '/?signin=failed')
        self.assertEqual(self.call('GET', '/api/auth/callback?error=access_denied&state=x', origin=False)[1]['Location'],
                         '/?signin=failed')

    def test_replay_and_allowlist(self):
        cookie, query = self.start()
        token = self.id_token(query['nonce'])
        self.assertEqual(self.callback(cookie, query, token)[1], '/')
        self.assertEqual(self.callback(cookie, query, token)[1], '/?signin=failed')  # the nonce is used up
        cookie, query = self.start()
        self.assertEqual(self.callback(cookie, query, self.id_token(query['nonce'], email='stranger@example.com'))[1],
                         '/?signin=not-allowed')

    def test_auth0_requires_its_client_settings(self):
        with patch.dict(os.environ, {'AUTH0_CLIENT_SECRET': ''}):
            self.assertEqual(self.call('GET', '/api/auth')[0], 503)
        self.assertEqual(self.call('POST', '/api/auth/login', body={'nonce': 'x', 'credential': 'y'})[0], 404)


class TenancyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patcher = patch.object(tenancy, '_runtimes', tenancy.collections.OrderedDict())
        patcher.start();self.addCleanup(patcher.stop)

    def test_the_workspace_lock_can_be_taken_twice_by_one_thread(self):
        finished = threading.Event()

        def nested():
            with tenancy.activate(self.root, 'c' * 64, server.tenant_save) as tenant:
                with tenant.locked(), tenant.locked():
                    finished.set()
        thread = threading.Thread(target=nested, daemon=True);thread.start();thread.join(5)
        self.assertTrue(finished.is_set(), 'nested locked() deadlocked')

    def test_the_lock_still_excludes_other_threads(self):
        with tenancy.activate(self.root, 'c' * 64, server.tenant_save) as tenant:
            entered = threading.Event()

            def other():
                with tenant.locked():
                    entered.set()
            worker = threading.Thread(target=other, daemon=True)
            with tenant.locked():
                worker.start()
                self.assertFalse(entered.wait(0.3))  # waits while this thread holds the lock
            self.assertTrue(entered.wait(5))  # and gets in once it is released
            worker.join(5)

    def test_a_save_outside_the_request_lands_in_the_users_own_folder(self):
        with tenancy.activate(self.root, 'd' * 64, server.tenant_save) as tenant:
            pass
        self.assertIsNone(tenancy.CURRENT.get())
        with patch.object(server, 'DATA', self.root), patch.object(server, 'STORE', None):
            state = server.main_seed();state['permissions']['dailyCap'] = 7
            tenant.workspace.persist(state)  # e.g. the crash-safe reservation, run with no request context
        self.assertEqual(json.loads((self.root / 'users' / ('d' * 64) / 'main.json').read_text())['permissions']['dailyCap'], 7)
        self.assertFalse((self.root / 'main.json').exists())

    def test_idle_workspaces_beyond_the_limit_are_dropped_but_active_ones_stay(self):
        with patch.object(tenancy, 'MAX_TENANTS', 3):
            with tenancy.activate(self.root, '0' * 64, server.tenant_save) as busy:
                for i in range(1, 8):
                    with tenancy.activate(self.root, str(i) * 64, server.tenant_save):
                        pass
                self.assertLessEqual(len(tenancy._runtimes), 4)
                self.assertIn(busy.directory, tenancy._runtimes)  # in use, so never evicted
            self.assertLessEqual(len(tenancy._runtimes), 4)


class GovernorLoopTests(unittest.TestCase):
    def test_idle_workspaces_are_skipped_without_loading_them(self):
        with tempfile.TemporaryDirectory() as root, patch.object(server, 'DATA', Path(root)), patch.object(server, 'STORE', None):
            uid = 'e' * 64
            self.assertFalse(server.tenant_has_governor_work(uid, False))  # nothing saved yet
            folder = Path(root) / 'users' / uid;folder.mkdir(parents=True)
            state = server.main_seed()
            server.tenant_save(state, folder)
            self.assertFalse(server.tenant_has_governor_work(uid, True))
            state['governorRequests'] = [{'status': 'queued'}]
            server.tenant_save(state, folder)
            self.assertTrue(server.tenant_has_governor_work(uid, False))
            state['governorRequests'] = [{'status': 'submitted'}]
            state['governorForecasts'] = [{'outcome': None}]
            server.tenant_save(state, folder)
            self.assertFalse(server.tenant_has_governor_work(uid, False))
            self.assertTrue(server.tenant_has_governor_work(uid, True))  # scoring is due
            (folder / server.GOVERNOR_STATUS).write_text('not json')
            self.assertTrue(server.tenant_has_governor_work(uid, False))  # unreadable marker counts as work
            (Path(root) / 'users' / uid / server.GOVERNOR_STATUS).unlink()
            self.assertTrue(server.tenant_has_governor_work(uid, False))  # older workspace without a marker


    def test_a_crash_between_the_marker_and_the_workspace_never_hides_work(self):
        with tempfile.TemporaryDirectory() as root, patch.object(server, 'DATA', Path(root)), patch.object(server, 'STORE', None):
            uid = 'f' * 64;folder = Path(root) / 'users' / uid;folder.mkdir(parents=True)
            idle = server.main_seed();server.tenant_save(idle, folder)
            self.assertFalse(server.tenant_has_governor_work(uid, False))
            real_write = server.write_json

            def crash_on_workspace(path, value):
                if path.name == 'main.json':raise OSError('disk full')
                return real_write(path, value)
            busy = server.main_seed();busy['governorRequests'] = [{'status': 'queued'}]
            with patch.object(server, 'write_json', side_effect=crash_on_workspace), self.assertRaises(OSError):
                server.tenant_save(busy, folder)
            self.assertTrue(server.tenant_has_governor_work(uid, False))  # a harmless extra pass, not a skip
            server.tenant_save(busy, folder)
            with patch.object(server, 'write_json', side_effect=crash_on_workspace), self.assertRaises(OSError):
                server.tenant_save(idle, folder)  # the work is still on disk, so the marker must still say so
            self.assertTrue(server.tenant_has_governor_work(uid, False))


class HostingTests(unittest.TestCase):
    def test_plain_http_on_a_lan_keeps_http_origins_but_still_needs_access_control(self):
        with patch.dict(os.environ, {'BIND_HOST': '0.0.0.0'}, clear=True):
            self.assertTrue(hosting.hosted());self.assertFalse(hosting.https())
            self.assertEqual(hosting.expected_origin('192.168.1.5:3110'), 'http://192.168.1.5:3110')
        with patch.dict(os.environ, {'BIND_HOST': '0.0.0.0', 'GOVERNTRADE_HTTPS': 'true'}, clear=True):
            self.assertEqual(hosting.expected_origin('trade.example.com'), 'https://trade.example.com')
        with patch.dict(os.environ, {'RENDER': 'true'}, clear=True):
            self.assertTrue(hosting.https())

    def test_ipv6_loopback_is_allowed_locally_only(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(hosting.allowed_host('[::1]:3110'))
        with patch.dict(os.environ, {'RENDER': 'true', 'RENDER_EXTERNAL_HOSTNAME': HOST}, clear=True):
            self.assertFalse(hosting.allowed_host('[::1]:3110'))


if __name__ == '__main__':
    unittest.main()
