# GovernTrade AI

A local venue-connected agent hub using the existing GovernPay visual style. Create trading agents, connect your own Kalshi accounts, and govern every proposal before execution. Funds remain at venues. The four-agent presenter demo is fully simulated and isolated.

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
- `DATABASE_URL`: optional Postgres connection string. When set, the main workspace (accounts, agents, mandates, orders, evaluations, events, chats) is stored in Postgres instead of `.data/main.json`; see Database storage below.

## Connect accounts

Connected Accounts → Connect account offers **Kalshi Demo** (authenticated virtual money at `external-api.demo.kalshi.co`) and **Kalshi Live** (real money at `external-api.kalshi.com`). Live requires an explicit real-money confirmation. Save an API key ID and RSA PEM private key, then **Test connection**. Demo and Live need their corresponding keys. Choose subaccount 0 (primary) or a numbered subaccount 1–63. All portfolio reads and orders are scoped to that subaccount. Existing connection environment and subaccount cannot be changed; create a separate connection instead. Rotate keys by editing; blank credentials retain encrypted keys. Disconnect pauses bound agents and removes credentials after outstanding submissions are reconciled.

## Balances and governance

Overview shows each connected Kalshi account with a Demo/Live label. `/portfolio/balance` supplies available cash and marked position value in cents, converted to dollars. Reserved cash uses `/portfolio/summary/total_resting_order_value` when available. That endpoint is FCM-only and unscoped; normal members and numbered subaccounts use paginated scoped resting orders, visibly labeled. Available cash already excludes venue reservations, so it is not reduced by those reservations again. The total card shows available cash plus positions; reserved cash is displayed separately.

P/L combines paginated positions, settlements and fills: venue realized position P/L less fees, settlement outcomes for tickers absent from positions, and unrealized P/L as marked open portfolio value less open position exposure. Fees are expensed in realized P/L. This is current API accounting, not a reconstructed historical tax ledger. Failed or incomplete reads retain visibly stale last-known balances and block execution. Refresh syncs on page load and after submissions. Funds remain at Kalshi; no main-workspace deposit, transfer or withdrawal endpoint exists.

Create an agent in this order: name, LLM provider, dependent model, provider API key, connected Kalshi account. There is no market category restriction. `static/providers.json` is the single provider/model catalog shared by backend and UI. Saving makes a read-only authenticated model lookup at the selected provider to verify key and model access; failure does not save the agent. Keys are encrypted and masked. Saving goes straight to mandate setup. An unfinished agent remains a draft and cannot trade.

Choose Conservative, Moderate or Aggressive to populate defaults, then adjust automatic trade limit, daily spending cap, minimum cash reserve and first-trade-in-new-market approval. Only the user can save or change a mandate. The chat shows its read-only summary and current usage, with an Edit mandate link.

Trading Permissions sets a shared daily spending ceiling and a kill switch that pauses every agent. Connections may have stricter spending ceilings. Mandate saving rejects limits above the effective ceiling. Every trade passes both the agent mandate and account ceiling; details label which layer required approval or blocked it. Approval rechecks current funds, quotes and policies; hard blocks cannot be overridden. Existing cumulative market, exposure, observed daily loss and slippage guardrails remain enforced. Budgets count authorized amounts conservatively; uncertain submissions retain reservations across restart. Daily loss is observed equity drawdown since the first sync that UTC day, not complete historical daily loss. External fills also count conservatively toward account spending. Underlying-account identity deduplication remains a TODO; do not connect the same underlying account using different keys as separate funding sources.

## Isolated presenter demo

Select one of four prebuilt agents first. Each displays its personality and mandate before proposing:

1. Conservative Economics Scout: $5 economics trade executes automatically.
2. Moderate Sports Analyst: $40 sports trade exceeds its automatic limit; review reasoning and approve.
3. Aggressive Momentum Trader: $80 already spent against a $100 daily cap; $30 proposal is blocked without override.
4. Moderate Discovery Agent: new-market approval enabled; review the first proposed trade and deny it.

Demo funds, agents, orders and state are entirely simulated and isolated from connected accounts. Back/restart replays only demo state. Scheduled/recurring execution remains absent.

## Agentic teammate

See [agent-config.md](agent-config.md) for the JSON schema, examples, worker authentication, instructions and governed proposal API. The AI worker is not implemented on this branch; saved chat instructions do not call a model or trade automatically. Provider/model access is validated on save; this does not start an AI worker.

`.data/` contains the private local store, encrypted vault and separate demo state; it is ignored by Git. Old main simulation data is preserved locally as `legacy-simulation.json` and replaced by an empty connected-account workspace. The store is serialized and atomic within a single server process, not a distributed database. Local API protections include host/origin validation, HttpOnly SameSite cookies and CSRF tokens. They are not isolation from other trusted processes running as the same OS user.

## Database storage

Without `DATABASE_URL` the app uses local JSON files exactly as before. With it, the main workspace is stored in Postgres (developed against Tiger Cloud/TimescaleDB); the presenter demo always stays in local JSON. Venue and provider credentials never leave the local encrypted vault: the database holds only opaque vault references and masks, so the same vault key (macOS Keychain or `GOVERNTRADE_MASTER_KEY`) is still required on any machine using the database.

`migrations/` holds the schema. The server applies pending migrations at startup; `DATABASE_URL=... python store.py migrate` applies them without starting the server. `events` and `balance_snapshots` are TimescaleDB hypertables (`balance_snapshots` is created for balance history but not yet written). Each request holds a Postgres advisory lock, so writers are serialized even across server processes; budget reservations are committed before venue submission, as with JSON. Only the most recent 500 events are loaded into the app; older events remain in the database.

With Tiger Cloud, run the server without printing the password: `DATABASE_URL="$(tiger db uri <service-id> --with-password)" python server.py`. Never commit the connection string or put it in a plaintext `.env` file. Trade records then live in your cloud database rather than only on this machine. Existing `.data/main.json` contents are not imported automatically.

## Verification and remaining work

```sh
python3 -m unittest discover -s . -p 'test_*.py' -v
node --check static/app.js
```

Governance, failures, current-rule approval rechecks, idempotency, encrypted vault, persistence, demo isolation and order payloads are covered by tests. Real authenticated connections/orders have **not** been exercised because no user venue credentials were supplied. Run authenticated venue demo acceptance tests before enabling real money. Remaining integration work: the teammate's AI worker; robust reconciliation of unknown submissions by client ID; venue-account identity deduplication; complete historical cost-basis reporting. Only supported opening buys are implemented.

Existing schema-2 agents migrate to inactive drafts requiring provider validation and a new user mandate. Unsupported old venue configurations remain private for audit continuity and are hidden from the UI. Older demo state resets to the four-agent demo.

If account save reports that macOS Keychain refused access (for example system error -50), the encrypted vault could not open; this is not a Kalshi authentication result. Start the server from your local Terminal with Keychain access, or supply the same `GOVERNTRADE_MASTER_KEY` through a secure environment. Never replace an existing encryption key without preserving it. Credential saving fails closed; plaintext fallback is not supported.

Account safety controls also expose maximum account exposure (`exposureCap`), observed daily drawdown (`dailyLossCap`), protected available cash (`cashFloor`), and live trading enabled (`liveTrading`). These are hard gates across all agents on each bound account, never approval triggers. Workspace settings apply to every account; connection overrides only tighten them (minimum caps, maximum cash floor, AND for live trading). Existing execution safeguards remain enforced even if a configured cap is higher. Automatic trade authorization and new-market approval stay exclusively in each agent mandate. Turning live trading off blocks new real-money orders; it does not cancel existing orders or liquidate positions.

Create agent opens Kalshi connection setup immediately when no verified connected account exists. Saving from this flow tests the connection before continuing to agent setup. Failed connection tests stay on Connected Accounts; retrying Test connection successfully resumes agent creation.

PR #7 review corrections: new connections inherit Workspace limits with an empty override. Observed daily loss is retained in a durable, environment-wide maximum-loss ledger (Demo and Live separately) until the next UTC day. Because verified Kalshi account identity is not available in the implemented API, this deliberately conservative gate also applies to distinct accounts in that environment. It prevents replacing credentials, connection records or subaccounts from resetting known losses. It does not discover losses before the first observed sync. Account disconnect requires a successful final sync. The ledger and all extended safety settings round-trip through JSON and Postgres.

CI workflow prepared locally but upload is pending GitHub workflow permission. It installs all requirements (including keyring), runs Python and Node regression tests, and supplies an ephemeral TimescaleDB service for database integration tests. Local runs without TEST_DATABASE_URL skip database tests explicitly. Onboarding tests cover cancellation, Escape, navigation away, failed connection tests and successful retries.
