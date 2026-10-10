# GovernTrade AI

A local venue-connected agent hub using the existing GovernPay visual style. Create trading agents, connect your own Kalshi/Coinbase accounts, and govern every proposal before execution. Funds remain at venues. The four-scenario presenter demo is fully simulated and isolated.

## Run

Python 3.12+ and Node.js for syntax checks:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python server.py
```

Open http://localhost:3110 and enter the local workspace. Set `PORT=3111` if another preview occupies 3110. The entry screen is a local welcome screen, not hosted authentication. Keep the server on localhost; do not expose it through a public tunnel.

Credentials are encrypted with Fernet. On macOS, the vault encryption key is stored automatically in macOS Keychain on first credential save. Other systems must supply `GOVERNTRADE_MASTER_KEY` containing a Fernet key through a secure environment/secret manager. Generate one with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` and store it securely, outside this repository. Do not commit it, paste it in chat, or save it in plaintext `.env` files. Keep the same key to decrypt existing credentials; back up the key and encrypted store securely. Plaintext keyring/file backends are refused.

Environment variables:

- `PORT`: optional, default 3110.
- `GOVERNTRADE_MASTER_KEY`: required if macOS Keychain is unavailable; no default plaintext key file.
- `GOVERNTRADE_AGENT_TOKEN`: required only for trusted worker HTTP integration; use a random secret. Without it, worker endpoints are disabled.

## Connect accounts

Connected Accounts → Connect account. Multiple named connections are supported. Default is sandbox. Switching to live requires explicitly checking "Enable live trading" for that individual connection. Save then **Test connection**. Rotate keys through account settings; leaving credential fields blank retains existing encrypted credentials. Disconnect deletes credentials and pauses bound agents. Outstanding/unknown submissions must be reconciled first.

- **Kalshi:** API key ID and an RSA PEM private key from [Kalshi account settings](https://kalshi.com/account/profile). Demo and live require their own corresponding keys. Signed requests use RSA-PSS and the current [Trade API specification](https://docs.kalshi.com/openapi.yaml). Balance sync includes cash, portfolio value, paginated positions, API-reported realized P/L and marked unrealized P/L derived from portfolio value minus position exposure. IOC binary opening orders use the current `/portfolio/events/orders` API and a conservative quadratic fee/rounding reserve. Unsupported fee types are hard blocked.
- **Coinbase:** full CDP key name (`organizations/.../apiKeys/...`) and an **ECDSA P-256** PEM private key from [CDP](https://portal.cdp.coinbase.com/access/api). Live uses short-lived ES256 JWTs. USD spot opening buys use price-limited FOK orders with fee reserves and venue preview. Unsupported fee/tax structures are rejected. Coinbase P/L is unavailable from the implemented account endpoint and displayed as such.
- **Other venue:** extension placeholder. A trusted adapter and credential form must be registered before connecting; arbitrary API hosts are not accepted.

Use trade-only keys and disable withdrawals. The app provides no deposit, withdrawal or transfer endpoints. These API keys are for the local owner's own account; a hosted multi-user Coinbase product needs its supported user authorization flow, not this localhost design.

## Balances and governance

Overview combines known venue balances with per-account sandbox/live/source labels. Sync runs on page load, after submission, and with Refresh. Failed fetches retain last-known values visibly marked **STALE** with an error; stale data never authorizes execution. No connected accounts means no invented balance. Main has no Add Funds.

Trading Permissions sets global defaults plus account/agent overrides. Overrides only tighten rules. Every execution passes `engine.checks()` using a fresh bound-account balance and venue quote. Approval rechecks rules and funds; hard blocks are never overridable. Shared spending caps, cumulative market caps, available cash, reserve, observed daily loss, external exposure, paused agents and quote validity are enforced. Uncertain submissions reserve budgets persistently. Budgets conservatively count authorized all-in amounts rather than recycling unused fills/proceeds.

Daily loss is observed equity drawdown from the first sync that UTC day, not complete venue historical daily P/L. Kalshi and Coinbase external fill turnover counts toward shared daily spending conservatively. Deposits/withdrawals can affect observed drawdown. Never connect the same underlying venue account twice and sum it as separate funds; automatic underlying-account identity detection is not yet implemented.

Create an agent with a name, category, connected compatible venue, provider/model and model API key. Open its chat to save instructions and read its Trading Mandate. Pause/edit/delete are available. Model and venue secrets are encrypted, never returned to the browser, and shown only as last-four masks.

## Sandbox and demo

[Coinbase's official sandbox](https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/sandbox) is static and unauthenticated: it cannot verify the keys or show your actual money. Its balances/orders are explicitly marked **STATIC MOCK**. Public live spot prices are used only as labeled quote references; sandbox order responses remain mocks. Kalshi's demo exchange is a separate authenticated virtual-money environment. Neither is combined silently with live-only funds.

DEMO has exactly four presenter-controlled scenarios: small BTC automatic fill; sports trade requiring approval and reasoning review; losing-day hard block; agent paused before its next proposal. Back/restart replays only isolated demo state. Demo-only Add Funds is available through the simulated customer account. Scheduled trades, frequencies, recurring execution and Policy Builder have been removed.

## Agentic teammate

See [docs/agent-config.md](docs/agent-config.md) for the JSON schema, examples, worker authentication, instructions and governed proposal API. The AI worker is not implemented on this branch; saved chat instructions do not call a model or trade automatically. Provider/model identifiers are user-supplied and are not claimed to be validated.

`.data/` contains the private local store, encrypted vault and separate demo state; it is ignored by Git. Old main simulation data is preserved locally as `legacy-simulation.json` and replaced by an empty connected-account workspace. The store is serialized and atomic within a single server process, not a distributed database. Local API protections include host/origin validation, HttpOnly SameSite cookies and CSRF tokens. They are not isolation from other trusted processes running as the same OS user.

## Verification and remaining work

```sh
python3 -m unittest discover -s . -p test_engine.py -v
node --check static/app.js
```

Governance, failures, current-rule approval rechecks, idempotency, encrypted vault, persistence, demo isolation and order payloads are covered by tests. Real authenticated connections/orders have **not** been exercised because no user venue credentials were supplied. Run authenticated venue demo acceptance tests before enabling real money. Remaining integration work: the teammate's AI worker; robust reconciliation of unknown submissions by client ID; venue-account identity deduplication; complete venue daily P/L and cost-basis reporting; additional venue adapters. Only supported opening buys are implemented.
