# GovernTrade AI

## AI prediction-market agents. Governed by you.

**Website:** [GovernTradeAI.us](https://governtradeai.us/)

**Repository:** [github.com/JakeYoung35/governtrade-ai](https://github.com/JakeYoung35/governtrade-ai)

GovernTrade AI is a platform for running AI trading agents on prediction markets without giving those agents unrestricted control of your money. Connect a supported trading account, configure an AI model or external agent, define its permissions, and let GovernTrade evaluate its proposals before execution.

**Agents propose. The portfolio governor allocates. Your rules control execution.**

> **Prototype status:** GovernTrade AI is under active development. It includes Kalshi Demo and Kalshi Live integration code, but is not a production-certified financial service. Trading involves risk, including loss of capital.

## What makes GovernTrade different?

An AI agent's trade recommendation is not the same thing as authorization to place an order. GovernTrade separates the two with a governance layer that:

- **Coordinates multiple agents** proposing trades against connected accounts.
- **Evaluates expected edge** using estimated probabilities, prices, liquidity, and fees.
- **Allocates capital** with reliability-adjusted fractional Kelly sizing and portfolio exposure limits.
- **Enforces user-defined mandates** such as automatic trade limits, daily spending caps, minimum cash reserves, and approval requirements.
- **Provides oversight** through trade decisions, explanations, activity records, and a kill switch.
- **Rejects or resizes trades** that do not satisfy current market conditions or the user's limits.

## How it works

```text
User / Web Dashboard
        |
        v
Configured AI Agent or External Worker
        |
        | Standardized trade proposal
        v
Portfolio Governor
  - Validate proposal and forecast
  - Read market liquidity and positions
  - Check forecast reliability and expected edge
  - Size positions and enforce portfolio caps
        |
        v
Trade Execution and User Mandates
  - Automatic execution / approval / block
  - Recheck current limits and market data
        |
        v
Kalshi Demo or Kalshi Live
        |
        v
Activity History, Positions, and Audit Records
```

**Illustrative example:** An agent proposes a $20 trade. The governor reduces it to $8 to fit portfolio limits. Because your automatic trading limit currently works out to $5, the proposal waits for your approval. Before submitting the order, GovernTrade checks your current rules, balance, and price again. A hard limit can still block execution even after approval.

The governor batches proposals separately for each connected account, ranks eligible proposals by reliability and estimated net edge, and sizes them against available cash, liquidity, fees, and exposure caps. Fractional Kelly sizing uses the agent's estimated probability and the contract's cost to calculate a stake, scaled by reliability and bounded by the user's budget and rules.

Eligible orders use immediate-or-cancel limit orders: contracts may fill only at or below the governor's limit price, and orders can fill partially or go unfilled. When markets settle, the agent's first forecast per market is scored using a Brier score, which measures probability forecast accuracy. That updates its reliability for future sizing; it does not retrain the model.

### Start using the app

1. Visit **[GovernTradeAI.us](https://governtradeai.us/)**.
2. Sign in if authentication is enabled for the hosted deployment.
3. Save your account-wide trading rules before enabling trading.
4. Connect a Kalshi Demo account, or explicitly connect Kalshi Live for real-money trading.
5. Create an agent with a supported AI model and your provider key, or choose the GovernTrade free agent when offered by the deployment.
6. Set the agent's mandate and give it trading instructions with an explicit dollar budget. Chat proposals can execute automatically when your mandate permits them.
7. Review its proposed, approved, blocked, and executed trades in the dashboard.

Trading rules and financial mandate limits use percentages of account value, converted to dollars when checked. New agents cannot trade until their mandates are saved, and chat refuses proposals without an identifiable budget.

The **in-app DEMO** is a separate scripted simulation with no exchange connection. **Kalshi Demo** uses a connected demo-exchange account and virtual funds. **Kalshi Live** uses real funds and requires explicit confirmation.


## Integration: bring your own agent

External agents can submit a normalized trade proposal to the governor rather than receiving direct authority to place orders.

**Deployment restriction:** The global worker token is disabled in personal hosted workspaces because it does not identify an individual user. External workers currently require a supported local or legacy shared-workspace configuration; user-scoped worker authentication is planned. In-app agent chat does not require an external worker.

```http
POST /api/governor/proposals
Authorization: Bearer <worker-token>
Content-Type: application/json
```

```json
{
  "schemaVersion": 1,
  "proposalId": "sample-agent:trade-001",
  "agentId": "your-configured-agent-id",
  "marketId": "VENUE-MARKET-TICKER",
  "side": "yes",
  "estimatedYesProbability": 0.65,
  "maxBudgetUsd": 10,
  "maxEntryPrice": 0.55,
  "reasoning": "The model estimates a 65% probability of YES."
}
```

The server validates the proposal, queues it for governance, and returns a proposal identifier for status lookup. The governor may approve, reduce, or reject the request. Authorization does **not** guarantee a market fill.

The API exposes `GET /api/governor/schema` for its current schema and `GET /api/governor/proposals/{governorProposalId}` for request status. The external worker interface uses a trusted bearer token; it does not provide per-agent token isolation. See [Agent integration contract](docs/agent-config.md) for requirements and deployment restrictions.

## Technology and repository layout

**Backend:** Python 3.12+, built-in HTTP server, API and workspace services\
**Frontend:** HTML, CSS, and JavaScript\
**AI integrations:** OpenAI, Anthropic, Gemini\
**Trading integration:** Kalshi Demo and Live APIs\
**Persistence:** JSON workspace storage and encrypted credential vaults; optional Postgres in legacy shared-workspace mode\
**Hosting configuration:** Render, with optional Auth0 or Google sign-in

| Path | Role |
| --- | --- |
| `server.py` | HTTP service, API endpoints, and background governor coordination |
| `agent_chat.py`, `agent_llm.py` | Agent conversations, model calls, and tool use |
| `governor_service.py` | Submission queue, batch evaluation, execution, and scoring |
| `governor/` | Validation, allocation, sizing, reliability, circuit breakers, and audit logic |
| `engine.py`, `workspace.py` | Trading state, mandates, permissions, and execution workflow |
| `adapters.py` | Kalshi integration and simulated venue adapter |
| `trading_rules.py` | User-defined account-wide limits |
| `authentication.py`, `tenancy.py`, `vault.py` | Authentication, workspace isolation, and encrypted credentials |
| `store.py`, `migrations/` | Persistence and database schema |
| `static/` | Browser application and styling |
| `replay/`, `scripts/replay_demo.py` | Offline paper-trading and reproducible governor scenarios |
| `test_*.py` | Automated test modules |

## Run locally

Requires **Python 3.12 or later**.

```bash
git clone https://github.com/JakeYoung35/governtrade-ai.git
cd governtrade-ai
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python server.py
```

On macOS, the vault key is created in Keychain. On Windows, Linux, or systems without Keychain, configure `GOVERNTRADE_MASTER_KEY` before saving credentials. Follow [Setup and operations](docs/setup-and-operations.md) for encryption-key setup and Windows commands.

Open [localhost:3110](http://localhost:3110/). If that port is unavailable, choose another:

```bash
PORT=3113 python server.py
```

Local workspace access is not equivalent to production user authentication. Keep a local development server on localhost and do not publish it with trading credentials or private API keys.

### Test and replay

```bash
python -m unittest discover -s . -p 'test_*.py' -v
python scripts/replay_demo.py
```

The replay demo is offline and uses simulated fills and recorded order books. It does not place live orders.

## Security and financial controls

- AI agents submit proposals rather than directly holding order-execution authority.
- Saved account and provider secrets are encrypted in the workspace vault.
- Trades are checked against the latest available rules, balances, and quotes.
- Pending approvals undergo governance checks again before execution.
- Duplicate proposals and uncertain execution states have safeguards; full reconciliation remains an active development area.
- A global pause control and loss circuit breaker provide additional restrictions.
- The platform does not offer deposits, withdrawals, or transfers.

## Documentation

- [Agent integration and proposal API](docs/agent-config.md)
- [Portfolio governor](docs/governor.md)
- [Trading rules](docs/trading-rules.md)
- [Setup and operations](docs/setup-and-operations.md)
- [Render hosting and custom domain](docs/render-hosting.md)
- [Collaboration](COLLABORATION.md)
- [Verification notes](VERIFICATION.md)

## Near-future innovations

Our next step is to make GovernTrade a platform where developers bring agents, users evaluate their performance, and the governor coordinates capital and risk across markets and exchanges.

1. **Bring Your Own Agent:** Expand the existing governed proposal API with secure user-scoped access, simpler onboarding, and integration tools for custom models and agent frameworks. Developers could connect their own agents while GovernTrade enforces the user's mandates, account limits, and approval requirements. The current worker API is available only in supported deployment modes; personal hosted workspaces still require user-scoped worker authentication.

2. **Backtesting & Paper-Trading Auditions:** Evaluate agents against historical market data and trial them with virtual funds before granting real-money permissions. Users could compare forecast accuracy, drawdowns, and rule compliance under consistent evaluation conditions. Historical backtests, simulated trading, and live results would remain clearly separated; each provides different evidence, and none guarantees future performance.

3. **Correlation-Aware Portfolio Allocation:** Build on existing event exposure caps to identify when different agents are effectively making the same underlying bet. For example, separate election contracts may depend on the same political outcome. The governor could account for those relationships when allocating capital, reducing concentrated exposure across an otherwise varied portfolio.

4. **Cross-Platform Support:** Extend governed trading beyond Kalshi to Polymarket and future venues, with unified risk management across connected accounts. Venue integrations would account for differences in fees, liquidity, and settlement rules so that similar-looking contracts are not automatically treated as equivalent.

5. **Agent Marketplace:** Let strategy developers publish agents and users discover, compare, and audition them using standardized performance records. Selected agents would operate under each user's own permissions. Built on agent integration and evaluation tools, the marketplace could attract more creators as users join, and offer users more strategies as creators contribute.

These are roadmap directions and extensions to existing capabilities, not committed release dates. The current governed proposal API does not support adding to existing positions, selling, or leverage.

---

**Explore the project:** [GovernTradeAI.us](https://governtradeai.us/)\
**Source code:** [JakeYoung35/governtrade-ai](https://github.com/JakeYoung35/governtrade-ai)
