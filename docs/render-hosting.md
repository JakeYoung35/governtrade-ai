# Hosting on Render

Build command: `pip install -r requirements.txt`

Start command: `python server.py`

Health check path: `/healthz`

Render supplies `PORT`, `RENDER=true`, and `RENDER_EXTERNAL_HOSTNAME`. The server binds to `0.0.0.0` on Render and continues to bind to `127.0.0.1` locally. Hostnames are checked explicitly; HTTPS origins and secure session cookies are used in hosted mode. It also stops accepting localhost and 127.0.0.1 as host names unless they are added to GOVERNTRADE_ALLOWED_HOSTS.

## Personal workspaces with Google sign-in

Set these in Render's environment settings:

- `GOVERNTRADE_GOOGLE_CLIENT_ID`: the public client ID of a Google **Web application** OAuth client.
- `GOVERNTRADE_ALLOWED_HOSTS`: `governtradeai.us,www.governtradeai.us` (the Render hostname is added automatically).
- `GOVERNTRADE_DATA_DIR`: `/var/data/governtrade` on an attached persistent disk mounted at `/var/data`.
- `GOVERNTRADE_PERSISTENT_STORAGE`: `true`, only after the disk is attached.
- `GOVERNTRADE_MASTER_KEY`: a fresh Fernet key, entered as a secret. Keep a secure backup; losing it makes saved credentials unreadable. Do not regenerate it on deploy.

Keep `DATABASE_URL` unset in this mode: the legacy Postgres schema is not tenant-scoped, and the server refuses to combine it with personal workspaces. Use one Render service instance with the attached disk. Scaling to multiple hosts requires a tenant-aware database and credential-storage migration.

In Google Auth Platform, configure the app branding and External audience, then create a Web application client with these **authorized JavaScript origins**:

- `https://governtradeai.us`
- `https://governtrade-ai.onrender.com` if the Render URL will also be used
- `http://localhost:3113` only if needed for local sign-in testing

This implementation uses the Google Identity Services popup and a server-verified ID token, not a redirect code flow, so no callback URL or OAuth client secret is required. Publish the consent app audience when ready for public access; a Testing audience only allows designated test users. Follow any consent-screen verification requirements Google displays.

The public page contains sign-in only. Workspace APIs require an unexpired server session. Google token signatures, audience, issuer, expiry, verified email and the browser-bound one-use nonce are checked. Tokens are not saved. Sessions are stored by hash in `sessions.sqlite3`; sign-out revokes them. Secure host-only HttpOnly cookies and per-session CSRF checks protect actions.

User state and encrypted vaults are stored under `users/<verified-subject-hash>/`, with thread-local context and file locks. Both background governor execution and simulated demos use the same isolation. Existing shared `main.json`, `demo.json` and vault files are never imported into user workspaces.

Legacy `GOVERNTRADE_AGENT_TOKEN` access is disabled in this mode because it has no user identity. The existing UI still saves chat instructions without generating AI proposals. Implement user-scoped worker authentication before enabling external automated agents.

Before switching the deployed branch, run the tests and verify Google sign-in, sign-out, two independent users, persistence across a restart and Kalshi Demo connection. Public trading launch also needs venue acceptance testing and order reconciliation; this change does not certify those features.

## Legacy shared workspace access

Without `GOVERNTRADE_GOOGLE_CLIENT_ID`, the app uses one shared workspace. On Render it stays locked (HTTP 503) unless `GOVERNTRADE_ACCESS_PASSWORD` is set in Render's secret environment settings. A configured workspace requires browser HTTP authentication with username `governtrade` and that password. Every workspace API and static file requires access, including HEAD requests. `/healthz` is public and reveals no workspace data. Authenticated worker tokens remain limited to the existing worker endpoints.

`GOVERNTRADE_ACCESS_PASSWORD` and `GOVERNTRADE_AGENT_TOKEN` must be long random secrets, because there is no lockout on failed password attempts. Generate each one with:

```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

`/api/agent/proposals` is disabled on hosted instances and returns HTTP 410, even with a valid worker token or the access password. Workers must submit through `/api/governor/proposals`.

Do not publish connected accounts as an unauthenticated shared workspace. Use the personal-workspace configuration above for individual sign-in. HTTP authentication here protects a private shared workspace; it does not provide individual users or signup.

## Custom domain and persistence

Add the verified custom domains to `GOVERNTRADE_ALLOWED_HOSTS`, separated by commas (for example `governtradeai.us,www.governtradeai.us`). Add the same domains in Render's custom-domain settings and configure the DNS records Render specifies at the domain's current DNS provider. Do not use wildcards.

Before connecting any venue or AI credentials, set `GOVERNTRADE_MASTER_KEY` to a Fernet encryption key in Render's secret environment settings. Never copy local accounts, credential files, or encryption keys into the repository. Set `GOVERNTRADE_DATA_DIR` to a persistent disk mount directory to preserve JSON state and encrypted credentials. A free Render instance has ephemeral storage and must not be used to retain connected account credentials or ongoing trading state. Postgres alone does not persist the credential vault.

The hosted instance must use its own `DATABASE_URL`; never share a database with a local instance. Each instance has its own vault key and cannot decrypt the other's credentials, so on every state refresh each instance would mark the other's accounts as error and stale in the shared database.

Setting `BIND_HOST=0.0.0.0` locally switches the app into hosted mode. Hosted mode expects `https://` origins, so a local `http://` browser session cannot save changes.

Keep the hosted instance separate from the local workspace. Use Kalshi Demo first and retain all agent and account governance limits. Hosting and user isolation do not make the prototype a production-certified trading service.
