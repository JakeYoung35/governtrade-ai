# Hosting on Render

Build command: `pip install -r requirements.txt`

Start command: `python server.py`

Health check path: `/healthz`

Render supplies `PORT`, `RENDER=true`, and `RENDER_EXTERNAL_HOSTNAME`. The server binds to `0.0.0.0` on Render and continues to bind to `127.0.0.1` locally. Hostnames are checked explicitly. On Render, HTTPS origins and secure session cookies are used. Elsewhere (`BIND_HOST=0.0.0.0` on a LAN or in Docker) the server still requires access control but keeps plain `http://` origins, unless `GOVERNTRADE_HTTPS=true` is set behind your own TLS proxy. It also stops accepting localhost and 127.0.0.1 as host names unless they are added to GOVERNTRADE_ALLOWED_HOSTS.

## Personal workspaces

Each person signs in with **Auth0** (recommended) or directly with **Google**. If `AUTH0_DOMAIN` is set, Auth0 is used; otherwise `GOVERNTRADE_GOOGLE_CLIENT_ID` enables Google sign-in.

### Auth0

In the Auth0 dashboard, create a **Regular Web Application**. Under its settings add:

- **Allowed Callback URLs:** `https://governtradeai.us/api/auth/callback` (and the Render URL's `/api/auth/callback` if used; `http://localhost:3110/api/auth/callback` for local testing)
- **Allowed Logout URLs:** `https://governtradeai.us/` (and the Render or localhost root if used)
- **Connections:** enable the sign-in methods you want (Google, email and password, and so on). Turn on email verification for password sign-ups: unverified addresses are refused.

Then set in Render:

- `AUTH0_DOMAIN`: the tenant domain, for example `governtrade.us.auth0.com` (or your Auth0 custom domain).
- `AUTH0_CLIENT_ID` and `AUTH0_CLIENT_SECRET` (secret) from the application's settings.

The server uses the authorization code flow with PKCE: `/api/auth/start` sends the browser to Auth0 with a signed, per-attempt cookie (state, nonce, PKCE verifier); `/api/auth/callback` exchanges the code server-side and verifies the RS256 ID token against Auth0's published keys (issuer, audience, expiry, nonce, verified email). Sign-out also ends the Auth0 session.

### Google (direct)

- `GOVERNTRADE_GOOGLE_CLIENT_ID`: the public client ID of a Google **Web application** OAuth client.

### Settings for either provider

- `GOVERNTRADE_ALLOWED_EMAILS` (**required when hosted**): who may sign in, as comma-separated addresses and `@domains` (for example `owner@example.com,@yourteam.com`), or `*` for any verified account. Removing an address ends its sessions and stops its background governor passes.
- `GOVERNTRADE_ALLOWED_HOSTS`: `governtradeai.us,www.governtradeai.us` (the Render hostname is added automatically).
- `GOVERNTRADE_DATA_DIR`: `/var/data/governtrade` on an attached persistent disk mounted at `/var/data`.
- `GOVERNTRADE_PERSISTENT_STORAGE`: `true`, only after the disk is attached.
- `GOVERNTRADE_MASTER_KEY`: a fresh Fernet key, entered as a secret. Keep a secure backup; losing it makes saved credentials unreadable. Do not regenerate it on deploy.

### Free Render project setup

For the project on a Free instance, set `GOVERNTRADE_ALLOW_EPHEMERAL_WORKSPACES=true`, set `GOVERNTRADE_DATA_DIR=/tmp/governtrade`, and leave `GOVERNTRADE_PERSISTENT_STORAGE` unset. Keep the same Auth0 configuration, master encryption key, and allowed hosts. Set `GOVERNTRADE_ALLOWED_EMAILS=*` to allow any verified account, or keep an explicit team list. This opt-in changes only the storage readiness check; token verification, per-user isolation, session revocation, CSRF protection, encryption, and trade governance remain enforced. No storage messaging is added to the interface.

Keep `DATABASE_URL` unset in this mode: the legacy Postgres schema is not tenant-scoped, and the server refuses to combine it with personal workspaces. Use one Render service instance with the attached disk. Scaling to multiple hosts requires a tenant-aware database and credential-storage migration.

In Google Auth Platform, configure the app branding and External audience, then create a Web application client with these **authorized JavaScript origins**:

- `https://governtradeai.us`
- `https://governtrade-ai.onrender.com` if the Render URL will also be used
- `http://localhost:3113` only if needed for local sign-in testing

This implementation uses the Google Identity Services popup and a server-verified ID token, not a redirect code flow, so no callback URL or OAuth client secret is required. Publish the consent app audience when ready for public access; a Testing audience only allows designated test users. Follow any consent-screen verification requirements Google displays.

The public page contains sign-in only. Workspace APIs require an unexpired server session. Provider token signatures, audience, issuer, expiry, verified email and the browser-bound one-use nonce are checked. Tokens are not saved. Login challenges are signed, expiring cookies (one per sign-in attempt, so several tabs work, and anonymous visitors write nothing to disk); a challenge's nonce is recorded only after the provider verifies the sign-in, so replays are refused while a transient provider error can be retried. The signing key is `auth-secret` on the data disk (or `GOVERNTRADE_SESSION_SECRET`). Sessions are stored by hash in `sessions.sqlite3`; sign-out revokes them. Secure host-only HttpOnly cookies and per-session CSRF checks protect actions.

User state and encrypted vaults are stored under `users/<verified-subject-hash>/`, with thread-local context and file locks; each workspace saves to its own folder even outside a request. Both background governor execution and simulated demos use the same isolation. The governor loop reads a small `governor-status.json` marker beside each workspace and skips idle ones without loading them. At most `GOVERNTRADE_MAX_LOADED_WORKSPACES` (default 256) idle workspaces stay in memory; their state is on disk. Existing shared `main.json`, `demo.json` and vault files are never imported into user workspaces.

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

In legacy shared-workspace mode, the hosted instance must use its own `DATABASE_URL`; never share a database with a local instance. In personal-workspace mode, leave `DATABASE_URL` unset, as described above. Each instance has its own vault key and cannot decrypt the other's credentials, so on every state refresh each instance would mark the other's accounts as error and stale in the shared database.

Setting `BIND_HOST=0.0.0.0` locally switches the app into hosted mode. Hosted mode expects `https://` origins, so a local `http://` browser session cannot save changes.

Keep the hosted instance separate from the local workspace. Use Kalshi Demo first and retain all agent and account governance limits. Hosting and user isolation do not make the prototype a production-certified trading service.
