"""Hosting configuration; a hosted shared workspace must have an access password."""
import base64
import binascii
import hmac
import os
from urllib.parse import urlsplit


def bind_host():
    return os.environ.get('BIND_HOST', '0.0.0.0' if os.environ.get('RENDER') == 'true' else '127.0.0.1')


def hosted():
    """Reachable beyond this machine, so access must be controlled (an access password or personal sign-in)."""
    return os.environ.get('RENDER') == 'true' or bind_host() not in ('127.0.0.1', 'localhost', '::1')


def ephemeral_workspaces():
    """Hosted personal workspaces on temporary storage (GOVERNTRADE_ALLOW_EPHEMERAL_WORKSPACES without a persistent
    disk). A restart erases the loss breaker, the day's opening value, today's spending and unconfirmed orders, which
    the safety limits depend on, so real-money (Kalshi Live) trading is refused in this mode."""
    return (hosted() and os.environ.get('GOVERNTRADE_ALLOW_EPHEMERAL_WORKSPACES') == 'true'
            and os.environ.get('GOVERNTRADE_PERSISTENT_STORAGE') != 'true')


def https():
    """Served over HTTPS: secure cookies and https:// origins. True on Render, or with GOVERNTRADE_HTTPS=true behind
    another TLS proxy. Binding to 0.0.0.0 alone (a LAN or Docker over plain http) keeps http:// so writes still work."""
    return os.environ.get('RENDER') == 'true' or os.environ.get('GOVERNTRADE_HTTPS') == 'true'


def allowed_host(authority):
    try:
        parsed = urlsplit('//'+authority)
        if not authority or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            return False
        parsed.port  # reject malformed ports
        hostname = parsed.hostname
    except ValueError:
        return False
    allowed = set() if hosted() else {'localhost', '127.0.0.1', '::1'}
    allowed.update(x.strip().lower() for x in os.environ.get('GOVERNTRADE_ALLOWED_HOSTS', '').split(',') if x.strip())
    render_host = os.environ.get('RENDER_EXTERNAL_HOSTNAME', '').strip().lower()
    if render_host:
        allowed.add(render_host)
    return hostname in allowed


def expected_origin(authority):
    return ('https://' if https() else 'http://')+authority


def password_ok(authorization):
    password = os.environ.get('GOVERNTRADE_ACCESS_PASSWORD', '')
    if not password or not authorization.startswith('Basic '):
        return False
    try:
        supplied = base64.b64decode(authorization[6:], validate=True)
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(supplied, ('governtrade:'+password).encode())
