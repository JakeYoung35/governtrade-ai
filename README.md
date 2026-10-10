# GovernTrade AI

A separate local simulation based on the existing GovernPay design. Five market agents share one investment account and an independent server-side governance engine.

## Kalshi demo execution

The standalone `kalshi_demo.py` CLI connects to Kalshi's mock-funds demo exchange
for balances, markets, quotes, limit orders, status and cancellation. See
[KALSHI_DEMO.md](KALSHI_DEMO.md) for credentials, installation and first-order
commands. This optional script has its own dependency and order journal; the
browser simulation below remains separate.

## Run

Requires Python 3.12 or later. No package installation is needed.

```sh
cd /Users/jakeyoung/Documents/Codex/2026-10-09/i-want-to-create-an-application/outputs/governtrade
python3 server.py
```

Open http://localhost:3110. Click **Enter demo workspace**. The email field is illustrative and is not saved or authenticated.

## Explore

- Add simulated funds, then select one of five agents.
- Crypto chat example: `Invest $10 in BTC 15-minute Up/Down, Yes.`
- An incomplete crypto request asks for the market and direction. Other markets can be selected in the structured form; exact market names are also supported by the parser.
- Review and confirm a $100 five-day mandate with a cumulative $5 per-market limit.
- **Advance Time → 1 day** runs due proposals with current rules. Each day's contracts have distinct market IDs. Within a market ID, split orders share the cumulative cap.
- Customer Permissions controls shared account boundaries. Above-threshold trades require approval; hard restrictions cannot be overridden.
- **DEMO** opens five isolated presenter-controlled scenarios. Ordinary account state is preserved.
- Transaction Monitoring opens detailed checks, order terms, fees and settlement records.

## Verification

```sh
python3 -m unittest discover -s . -p test_engine.py -v
node --check static/app.js
```

## Simulation details and limits

All prices, outcomes, funds, authentication and trading are simulated. The offline parser is deterministic, not an LLM or a forecasting service. Fixed quotes are refreshed by the simulation clock. Profit/loss changes through fees and deterministic settlement; there is no live price feed. The broker immediately fills authorized orders; intermediate submitted/open states are recorded during processing, and no resting or partial fills are simulated. Denied, blocked, filled and settled orders remain distinct.

Scheduled trading runs only through Advance Time. No autonomous wall-clock worker runs when the browser or server is closed. Advancing past expiry stops the mandate; missed intervals are not replayed. Existing positions settle on time advancement even when future trading is paused.

The JSON store uses a process-wide lock and atomic file replacement to serialize concurrent actions for this single-process local server. It is not a multi-server database. `.data/` stores separate main and demo environments. Accounts, chats, mandates, policies, trade records, positions and events survive refreshes and server restarts. No real user isolation, credentials, custody, external notifications or live trading are implemented.

`adapters.py` provides replaceable market quote and broker fill/settlement adapters. A real integration needs authenticated user accounts, provider-supported execution and reconciliation, secure funding/custody, eligibility checks and venue requirements. This local server is not intended for public exposure.

Spending includes the 1% simulated fee. Mandate proceeds never replenish lifetime budget. Account value equals cash plus open marked position value. Total Simulated Investments is cumulative filled purchase cost, including fees; current exposure is displayed separately. All analytics derive from the workspace ledger without seeded historical counters.
