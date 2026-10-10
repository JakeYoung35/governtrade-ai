"""Google identity verification and revocable server sessions. No provider tokens are stored."""
import contextlib
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from http.cookies import SimpleCookie, CookieError
from pathlib import Path

SESSION_SECONDS = 8 * 60 * 60
CHALLENGE_SECONDS = 10 * 60


def enabled():
    return bool(os.environ.get('GOVERNTRADE_GOOGLE_CLIENT_ID'))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def cookie_value(header, name):
    try:
        cookies = SimpleCookie()
        cookies.load(header)
        return cookies[name].value if name in cookies else ''
    except CookieError:
        return ''


def verify_identity(credential, client_id, nonce):
    from functools import partial
    from google.auth.exceptions import GoogleAuthError
    from google.auth.transport.requests import Request
    from google.oauth2.id_token import verify_oauth2_token
    try:
        claims = verify_oauth2_token(credential, partial(Request(), timeout=10), client_id)
    except GoogleAuthError as exc:
        raise ValueError('Invalid sign-in.') from exc
    if (claims.get('iss') not in ('accounts.google.com', 'https://accounts.google.com')
            or claims.get('aud') != client_id or claims.get('exp', 0) <= time.time()
            or not isinstance(claims.get('sub'), str) or not claims['sub']
            or claims.get('email_verified') is not True
            or not isinstance(claims.get('nonce'), str)
            or not hmac.compare_digest(claims['nonce'], nonce)):
        raise ValueError('Invalid sign-in.')
    # A stable verified Google subject, never an email address or a client-supplied user ID.
    return {'id': digest('https://accounts.google.com:' + claims['sub']),
            'name': str(claims.get('name') or 'Account owner')[:200],
            'email': str(claims.get('email') or '')[:320]}


class AuthStore:
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / 'sessions.sqlite3'
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, name TEXT, email TEXT);
                CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id), csrf TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS challenges (token_hash TEXT PRIMARY KEY,
                    nonce TEXT NOT NULL, expires REAL NOT NULL);
            ''')
        os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:yield db
        finally:db.close()

    def challenge(self):
        token, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute('DELETE FROM challenges WHERE expires <= ?', (time.time(),))
            db.execute('DELETE FROM sessions WHERE expires <= ?', (time.time(),))
            # Bound anonymous disk use. Old challenges may require a page reload.
            db.execute('DELETE FROM challenges WHERE token_hash IN '
                       '(SELECT token_hash FROM challenges ORDER BY expires DESC LIMIT -1 OFFSET 1000)')
            db.execute('INSERT INTO challenges VALUES (?, ?, ?)',
                       (digest(token), nonce, time.time() + CHALLENGE_SECONDS))
        return token, nonce

    def login(self, challenge_token, nonce, credential, client_id):
        if not challenge_token or not isinstance(nonce, str) or not isinstance(credential, str):
            raise ValueError('Reload the sign-in page and try again.')
        # Consume once, transactionally, before contacting Google; concurrent replay cannot create a session.
        with self.connection() as db:
            row = db.execute('DELETE FROM challenges WHERE token_hash=? AND expires>? RETURNING nonce',
                             (digest(challenge_token), time.time())).fetchone()
        if not row or not hmac.compare_digest(row['nonce'], nonce):
            raise ValueError('Reload the sign-in page and try again.')
        user = verify_identity(credential, client_id, nonce)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.connection() as db:
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
        return dict(row) if row else None

    def revoke(self, token):
        with self.connection() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (digest(token),))

    def users(self):
        with self.connection() as db:
            return [r['id'] for r in db.execute('SELECT id FROM users')]
