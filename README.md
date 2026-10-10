# GovernTrade AI

## Let AI agents trade for you. Stay in control.

GovernTrade AI is a platform for giving AI trading agents permission to act on your behalf in prediction markets—with boundaries you set and checks enforced before each trade.

You choose the agent, tell it what you want, and decide how much freedom it has. GovernTrade checks each proposed trade against your rules before it can reach the trading venue.

**Our vision includes markets such as Polymarket. The current integration supports Kalshi Demo and Kalshi Live; Polymarket is not connected yet.**

## What problem does this solve?

An AI agent can propose a trade. That does not mean it should have unrestricted access to your money.

GovernTrade separates the agent’s trading decisions from its authority to spend. The agent proposes; an independent governance layer checks your limits. You can let smaller trades proceed automatically, require approval where you choose, and stop trades that break your account’s safety rules.

The purpose is controlled delegation: let an agent do the work while you remain in charge of the permissions.

## How it works

1. **Connect your trading account.** Start with Kalshi Demo using virtual funds, or connect Kalshi Live after explicitly confirming that real money will be used. Your funds stay at Kalshi.
2. **Create your agent.** Give it a name, choose an AI provider and model, and enter your provider API key. The app checks access to that model before saving.
3. **Set its mandate.** A mandate is the set of rules you give an agent. Choose a Conservative, Moderate or Aggressive preset, then adjust it. An agent cannot trade until you save its mandate.
4. **Give it instructions.** Describe the trade you want it to consider. The intended experience is a conversation with your agent.
5. **Check every proposal.** A trade must satisfy both the agent’s mandate and the account’s safety controls before execution. The activity history explains what happened and why.

**Current build status:** account connections, encrypted credential storage, mandates, governance checks, and a simulated demo are implemented. The AI worker is still pending: typing into chat currently saves your instructions; it does not independently call a model or start trading. A trusted worker can submit proposals through the governed API.

## Two layers of control

| You control | What it does |
| --- | --- |
| **Each agent’s mandate** | Sets its automatic trade limit, daily spending cap, minimum cash reserve, and whether a new market needs approval. |
| **Account-wide safety controls** | Limit total spending, position exposure and observed daily loss; protect cash; disable live trading; or pause all agents. |

Account controls do **not** add approval requests to trades your agent’s mandate already permits. They are hard safety boundaries: if a trade breaches one, it is blocked and the reason is shown. Agent mandates can be stricter than the account ceiling. Only you can change them—the agent cannot grant itself more authority.

## Protecting access to your account

- Venue and AI-provider credentials are encrypted locally and displayed only as masks.
- Trading uses the account bound to the saved agent; the model cannot choose another account or supply its own balances, approvals or policy overrides.
- Execution checks fresh account data and market quotes. Missing or stale data cannot authorize a trade.
- Approving a trade triggers another check. Hard blocks cannot be approved away.
- Uncertain submissions retain their spending reservations to help prevent duplicate or excessive spending.
- The app exposes no deposit, withdrawal or transfer tools.

These controls are designed to limit an agent’s authority. They do not guarantee profitable trades or eliminate investment risk. This is a local prototype, not a production-certified financial service, and authenticated venue acceptance testing remains outstanding.

## Try the demo

The **DEMO** button opens a fully simulated workspace separate from your connected accounts. Pick an agent and see four different outcomes: an automatic trade, a trade you approve, a spending-cap block, and a new-market proposal you deny.

No demo trade uses real funds.

## Run it locally

Use Python 3.12 or later:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python server.py
```

Open [localhost:3110](http://localhost:3110/). Keep the terminal running. If that port is busy, run `PORT=3113 python server.py` and open [localhost:3113](http://localhost:3113/).

Keep the server on localhost. The welcome screen is local workspace entry, not a hosted user login. Never commit credentials or paste private keys into chat.

## What comes next?

The remaining work includes AI-worker integration, Polymarket connectivity, reliable reconciliation of uncertain orders, and authenticated acceptance testing before relying on real-money execution. Scheduled multi-day trading is not implemented.

Observed daily loss begins with the first successful daily sync. To prevent reconnection from clearing known losses, the app conservatively retains the worst observed loss across connections in the same Demo or Live environment until the next UTC day. This can also constrain separate accounts.

For installation details, Keychain troubleshooting, database setup, accounting limitations and test commands, see [Setup and operations](docs/setup-and-operations.md). Developers integrating an agent should read the [Agent integration contract](docs/agent-config.md).
