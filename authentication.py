"""Sign-in through Auth0 or Google, and revocable server sessions. No provider tokens are stored.

Login challenges are signed, expiring cookies rather than database rows: every tab gets its own, anonymous visitors
cannot write to the disk or evict anyone else's pending sign-in, and a challenge survives a transient provider error.
A challenge's nonce is recorded as used only after the provider has verified the sign-in, so it cannot be replayed.
"""
import base64
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SESSION_SECONDS = 8 * 60 * 60
CHALLENGE_SECONDS = 10 * 60
PROVIDER_TIMEOUT = 10


def provider():
    """'auth0' or 'google' when personal workspaces are configured, else None (local single-user mode)."""
    if os.environ.get('AUTH0_DOMAIN'):
        return 'auth0'
    if os.environ.get('GOVERNTRADE_GOOGLE_CLIENT_ID'):
        return 'google'
    return None


def enabled():
    return provider() is not None


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def cookie_value(header, name):
    """One cookie from a Cookie header. Parsed by hand: http.cookies stops at the first cookie it cannot parse, so
    an unrelated cookie (a JSON value, a space) from another app on the same host would hide the session."""
    for part in (header or '').split(';'):
        key, sep, value = part.strip().partition('=')
        if sep and key == name:
            return value.strip().strip('"')
    return ''


def email_allowed(email):
    """GOVERNTRADE_ALLOWED_EMAILS: comma-separated addresses and @domains, or * for any verified account. Unset
    allows everyone; hosted mode refuses to start personal workspaces without it (see server.multiuser_ready)."""
    rule = os.environ.get('GOVERNTRADE_ALLOWED_EMAILS', '').strip()
    if not rule or rule == '*':
        return True
    email = (email or '').strip().lower()
    entries = {x.strip().lower() for x in rule.split(',') if x.strip()}
    return bool(email) and (email in entries or '@' + email.rpartition('@')[2] in entries)


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


# Google: the browser receives an ID token from Google Identity Services and posts it to /api/auth/login.

def verify_google(credential, client_id, nonce):
    from functools import partial
    from google.auth.exceptions import GoogleAuthError
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.id_token import verify_oauth2_token
    try:
        # Checks the signature, audience, expiry and issuer.
        claims = verify_oauth2_token(credential, partial(GoogleRequest(), timeout=PROVIDER_TIMEOUT), client_id)
    except GoogleAuthError as exc:
        raise ValueError('Invalid sign-in.') from exc
    return _identity('https://accounts.google.com', claims, nonce)


verify_identity = verify_google  # the original name, kept for callers and tests


# Auth0: authorization code flow with PKCE. The server exchanges the code and verifies Auth0's signed ID token.

def auth0_config():
    domain = os.environ['AUTH0_DOMAIN'].strip().removeprefix('https://').strip('/')
    return {'domain': domain, 'issuer': f'https://{domain}/', 'client_id': os.environ['AUTH0_CLIENT_ID'],
            'client_secret': os.environ.get('AUTH0_CLIENT_SECRET', '')}


def auth0_authorize_url(flow, redirect_uri):
    cfg = auth0_config()
    challenge = _b64(hashlib.sha256(flow['verifier'].encode()).digest())
    return f"https://{cfg['domain']}/authorize?" + urlencode({
        'response_type': 'code', 'client_id': cfg['client_id'], 'redirect_uri': redirect_uri,
        'scope': 'openid profile email', 'state': flow['state'], 'nonce': flow['nonce'],
        'code_challenge': challenge, 'code_challenge_method': 'S256'})


def auth0_logout_url(return_to):
    cfg = auth0_config()
    return f"https://{cfg['domain']}/v2/logout?" + urlencode({'client_id': cfg['client_id'], 'returnTo': return_to})


def auth0_exchange(code, verifier, redirect_uri):
    """The ID token for an authorization code. Raises OSError/ValueError on a provider or network failure."""
    cfg = auth0_config()
    body = {'grant_type': 'authorization_code', 'client_id': cfg['client_id'], 'code': code,
            'code_verifier': verifier, 'redirect_uri': redirect_uri}
    if cfg['client_secret']:
        body['client_secret'] = cfg['client_secret']
    request = Request(f"https://{cfg['domain']}/oauth/token", data=urlencode(body).encode(), method='POST',
                      headers={'Content-Type': 'application/x-www-form-urlencoded'})
    with urlopen(request, timeout=PROVIDER_TIMEOUT) as response:
        token = json.load(response).get('id_token')
    if not isinstance(token, str):
        raise ValueError('Auth0 returned no ID token.')
    return token


_jwks_clients = {}


def auth0_signing_key(token):
    import jwt
    cfg = auth0_config()
    url = f"https://{cfg['domain']}/.well-known/jwks.json"
    client = _jwks_clients.get(url) or _jwks_clients.setdefault(url, jwt.PyJWKClient(url, timeout=PROVIDER_TIMEOUT))
    return client.get_signing_key_from_jwt(token).key


def verify_auth0(id_token, nonce):
    import jwt
    cfg = auth0_config()
    try:
        claims = jwt.decode(id_token, auth0_signing_key(id_token), algorithms=['RS256'], audience=cfg['client_id'],
                            issuer=cfg['issuer'], options={'require': ['exp', 'iat', 'sub', 'aud', 'iss']})
    except jwt.PyJWTError as exc:
        raise ValueError('Invalid sign-in.') from exc
    return _identity(cfg['issuer'], claims, nonce)


def _identity(issuer, claims, nonce):
    if (not isinstance(claims.get('sub'), str) or not claims['sub']
            or claims.get('email_verified') is not True
            or not isinstance(claims.get('nonce'), str) or not hmac.compare_digest(claims['nonce'], nonce)):
        raise ValueError('Invalid sign-in.')
    # A stable verified subject from the provider, never an email address or a client-supplied user ID.
    return {'id': digest(issuer.rstrip('/') + ':' + claims['sub']),
            'name': str(claims.get('name') or 'Account owner')[:200],
            'email': str(claims.get('email') or '')[:320]}


class NotAllowed(ValueError):
    """A verified account that GOVERNTRADE_ALLOWED_EMAILS does not admit."""


class AuthStore:
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / 'sessions.sqlite3'
        self.secret = self._secret(directory / 'auth-secret')
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, name TEXT, email TEXT);
                CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id), csrf TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS used_nonces (nonce_hash TEXT PRIMARY KEY, expires REAL NOT NULL);
                DROP TABLE IF EXISTS challenges;
            ''')
        os.chmod(self.path, 0o600)

    @staticmethod
    def _secret(path):
        """The key that signs login challenges, shared by every process using this data disk."""
        configured = os.environ.get('GOVERNTRADE_SESSION_SECRET', '')
        if configured:
            return configured.encode()
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return path.read_bytes()
        with os.fdopen(fd, 'wb') as f:
            value = secrets.token_bytes(32)
            f.write(value)
        return value

    @contextlib.contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:yield db
        finally:db.close()

    # Signed challenges.

    def seal(self, payload):
        body = _b64(json.dumps({**payload, 'exp': time.time() + CHALLENGE_SECONDS}, separators=(',', ':')).encode())
        return body + '.' + _b64(hmac.new(self.secret, body.encode(), hashlib.sha256).digest())

    def unseal(self, token):
        body, _, signature = (token or '').partition('.')
        expected = _b64(hmac.new(self.secret, body.encode(), hashlib.sha256).digest())
        if not body or not hmac.compare_digest(signature, expected):
            raise ValueError('Reload the sign-in page and try again.')
        payload = json.loads(_unb64(body))
        if payload.get('exp', 0) <= time.time():
            raise ValueError('Reload the sign-in page and try again.')
        return payload

    def challenge(self):
        """A Google sign-in challenge: (signed cookie value, nonce). Writes nothing."""
        nonce = secrets.token_urlsafe(32)
        return self.seal({'nonce': nonce}), nonce

    def auth0_flow(self):
        """An Auth0 sign-in flow: (signed cookie value, {state, nonce, verifier}). Writes nothing."""
        flow = {'state': secrets.token_urlsafe(24), 'nonce': secrets.token_urlsafe(32),
                'verifier': secrets.token_urlsafe(48)}
        return self.seal(flow), flow

    def _consume(self, nonce):
        """Record a verified sign-in's nonce; a second use is a replay."""
        now = time.time()
        with self.connection() as db:
            db.execute('DELETE FROM used_nonces WHERE expires <= ?', (now,))
            try:
                db.execute('INSERT INTO used_nonces VALUES (?, ?)', (digest(nonce), now + CHALLENGE_SECONDS))
            except sqlite3.IntegrityError:
                raise ValueError('Reload the sign-in page and try again.') from None

    # Sessions.

    def login(self, challenge_token, nonce, credential, client_id):
        """Google: verify the posted ID token against its signed challenge, then create a session."""
        if not isinstance(nonce, str) or not isinstance(credential, str):
            raise ValueError('Reload the sign-in page and try again.')
        payload = self.unseal(challenge_token)
        if not hmac.compare_digest(payload.get('nonce', ''), nonce):
            raise ValueError('Reload the sign-in page and try again.')
        return self.start_session(verify_identity(credential, client_id, nonce), nonce)

    def login_auth0(self, flow_token, state, code, redirect_uri):
        """Auth0: check the callback against its signed flow, exchange the code, then create a session."""
        flow = self.unseal(flow_token)
        if not isinstance(state, str) or not hmac.compare_digest(flow.get('state', ''), state) or not code:
            raise ValueError('Reload the sign-in page and try again.')
        identity = verify_auth0(auth0_exchange(code, flow['verifier'], redirect_uri), flow['nonce'])
        return self.start_session(identity, flow['nonce'])

    def start_session(self, user, nonce):
        if not email_allowed(user['email']):
            raise NotAllowed('This account is not on the allowed list for this workspace.')
        self._consume(nonce)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute('DELETE FROM sessions WHERE expires <= ?', (time.time(),))
            db.execute('INSERT INTO users VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE '
                       'SET name=excluded.name, email=excluded.email', (user['id'], user['name'], user['email']))
            db.execute('INSERT INTO sessions VALUES (?, ?, ?, ?)',
                       (digest(token), user['id'], csrf, time.time() + SESSION_SECONDS))
        return token, {**user, 'csrf': csrf}

    def session(self, token):
        if not token:
            return None
        with self.connection() as db:
            row = db.execute('SELECT users.*, sessions.csrf FROM sessions JOIN users '
                             'ON users.id=sessions.user_id WHERE token_hash=? AND expires>?',
                             (digest(token), time.time())).fetchone()
        # Removing an address from the allowlist ends its existing sessions too.
        return dict(row) if row and email_allowed(row['email']) else None

    def revoke(self, token):
        with self.connection() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (digest(token),))

    def users(self):
        with self.connection() as db:
            return [r['id'] for r in db.execute('SELECT id, email FROM users') if email_allowed(r['email'])]
