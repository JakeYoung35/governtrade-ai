# Hosting on Render

Build command: `pip install -r requirements.txt`

Start command: `python server.py`

Health check path: `/healthz`

Render supplies `PORT`, `RENDER=true`, and `RENDER_EXTERNAL_HOSTNAME`. The server binds to `0.0.0.0` on Render and continues to bind to `127.0.0.1` locally. Hostnames are checked explicitly; HTTPS origins and secure session cookies are used in hosted mode. It also stops accepting localhost and 127.0.0.1 as host names unless they are added to GOVERNTRADE_ALLOWED_HOSTS.

## Shared workspace access

This app has one shared workspace, not separate user accounts. On Render it stays locked (HTTP 503) unless `GOVERNTRADE_ACCESS_PASSWORD` is set in Render's secret environment settings. A configured workspace requires browser HTTP authentication with username `governtrade` and that password. Every workspace API and static file requires access, including HEAD requests. `/healthz` is public and reveals no workspace data. Authenticated worker tokens remain limited to the existing worker endpoints.

`GOVERNTRADE_ACCESS_PASSWORD` and `GOVERNTRADE_AGENT_TOKEN` must be long random secrets, because there is no lockout on failed password attempts. Generate each one with:

```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

`/api/agent/proposals` is disabled on hosted instances and returns HTTP 410, even with a valid worker token or the access password. Workers must submit through `/api/governor/proposals`.

Do not publish connected accounts as an unauthenticated shared workspace. A public preview without account connections or a multiuser system requires a separate implementation. HTTP authentication here protects a private shared workspace; it does not provide individual users or signup.

## Custom domain and persistence

Add the verified custom domains to `GOVERNTRADE_ALLOWED_HOSTS`, separated by commas (for example `governtradeai.us,www.governtradeai.us`). Add the same domains in Render's custom-domain settings and configure the DNS records Render specifies at the domain's current DNS provider. Do not use wildcards.

Before connecting any venue or AI credentials, set `GOVERNTRADE_MASTER_KEY` to a Fernet encryption key in Render's secret environment settings. Never copy local accounts, credential files, or encryption keys into the repository. Set `GOVERNTRADE_DATA_DIR` to a persistent disk mount directory to preserve JSON state and encrypted credentials. A free Render instance has ephemeral storage and must not be used to retain connected account credentials or ongoing trading state. Postgres alone does not persist the credential vault.

The hosted instance must use its own `DATABASE_URL`; never share a database with a local instance. Each instance has its own vault key and cannot decrypt the other's credentials, so on every state refresh each instance would mark the other's accounts as error and stale in the shared database.

Setting `BIND_HOST=0.0.0.0` locally switches the app into hosted mode. Hosted mode expects `https://` origins, so a local `http://` browser session cannot save changes.

Keep the hosted instance separate from the local workspace. Use Kalshi Demo first and retain all agent and account governance limits. This hosting fix does not make the app a production multiuser service.
