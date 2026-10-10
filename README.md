# GovernTrade AI

## Let AI agents trade for you. Stay in control.

GovernTrade AI is a platform for allowing AI agents to trade prediction markets on your behalf, with financial boundaries you set and independent checks before every trade.

You choose your agent, give it instructions, and decide how much freedom it has. Before a proposed trade can execute, GovernTrade checks that it follows your agent’s mandate and your account’s safety controls.

**Our vision includes Polymarket. The current integration supports Kalshi Demo and Kalshi Live; Polymarket connectivity is planned.**

## Why GovernTrade?

Giving an AI agent trading instructions should not mean giving it unrestricted access to your money.

GovernTrade separates an agent’s ability to propose trades from its permission to execute them. You can authorize smaller trades automatically, require approval for specific situations, and block trades that exceed your financial boundaries.

**The agent proposes. Governance checks. You stay in charge.**

## How it works

1. **Connect your account.** Use Kalshi Demo with virtual funds or connect Kalshi Live after explicitly confirming that real money will be used. Your funds remain at Kalshi.
2. **Create your agent.** Name it, select an AI provider and model, and securely enter your provider API key.
3. **Set its mandate.** Choose a Conservative, Moderate, or Aggressive preset, then customize its limits. An agent cannot trade until its mandate is saved.
4. **Give it instructions.** Describe the markets and trades you want it to consider.
5. **Govern every proposal.** Each trade must satisfy both the agent’s mandate and the account’s safety controls. The activity history shows the outcome and explains the decision.

## Your agent’s mandate

Every agent has its own rules:

- **Automatic trade limit:** how much it can spend on a trade without asking for approval.
- **Daily spending cap:** how much it can spend each day.
- **Minimum cash reserve:** how much cash it must leave available.
- **New-market approval:** whether it needs permission before trading a market it has never traded before.

Only you can change these rules. An agent cannot expand its own permissions.

## Account-wide protection

Account controls protect your funds across all agents:

- Total daily spending ceiling.
- Maximum position exposure.
- Maximum observed daily loss.
- Protected cash balance.
- Live trading on/off.
- A kill switch that pauses every agent.

These controls do **not** add approval requests to trades already permitted by an agent’s mandate. Instead, they act as hard boundaries: a trade that violates an account limit is blocked, with the reason displayed.

Agent mandates can be stricter than account limits, but cannot override them.

## Security and execution safeguards

- Account and AI-provider credentials are encrypted in each workspace’s vault and displayed only as masks. Hosted credentials stay on persistent server storage; local credentials stay on your computer.
- Agents trade through their saved account; they cannot select a different account or supply their own approvals, balances, or policy overrides.
- Execution requires fresh account data and market quotes.
- Approving a trade triggers another governance check.
- Hard blocks cannot be approved away.
- Uncertain submissions retain spending reservations to help prevent duplicate or excessive spending.
- The app provides no deposit, withdrawal, or transfer tools.

These safeguards limit an agent’s authority. They do not guarantee profits or eliminate trading risk.

## Try the demo

Select **DEMO** to explore a simulated workspace that is separate from your connected accounts.

Choose one of four agents and see:

1. A small trade execute automatically.
2. A trade wait for your approval.
3. A trade blocked for exceeding its daily spending cap.
4. A new-market proposal that you review and deny.

**Demo trades never use real funds.**

## Current development status

Implemented:

- Kalshi Demo and Live account connections.
- Encrypted credential storage.
- Agent configuration and provider-key validation.
- Individual agent mandates.
- Account-wide safety controls.
- Governed trade-proposal and execution endpoints.
- Agent chat: the agent's own model (OpenAI, Anthropic or Gemini) reads your message, finds the Kalshi market and proposes a trade. The portfolio governor sizes it, and your mandate decides what runs automatically.
- Trade activity and decision history.
- An isolated simulated demo.

Still in development:

- Polymarket connectivity.
- Scheduled and multi-day trading.
- Reliable reconciliation of uncertain order submissions.
- Authenticated venue acceptance testing.

**Chat calls the agent's model with its saved API key and can propose real trades on the agent's connected account.** It never places orders directly: every proposal goes through the portfolio governor, the agent mandate and the account ceiling, and trades above the automatic limit wait for your approval. A trusted external worker can also submit proposals through the governed API.

This is a prototype, not a production-certified financial service. The Google sign-in implementation adds personal workspaces; its hosting configuration and end-to-end sign-in must be completed before opening public account connections.

## Run locally

Use Python 3.12 or later:

    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -r requirements.txt
    python server.py

Open http://localhost:3110/ and keep the terminal running.

If the port is already in use:

    PORT=3113 python server.py

Then open http://localhost:3113/.

Keep the server on localhost. The welcome screen is local workspace entry, not hosted user authentication. Never commit credentials or share private keys in chat.

## Personal accounts on the hosted app

When Google sign-in is configured, each person signs in with their own Google account. There is no shared website password. Each user gets separate connected accounts, encrypted credentials, agents, mandates, approvals, trade history and simulated demo state. Signing out revokes that session; sessions expire after eight hours.

The backend verifies Google's signed identity token and derives the workspace identity from Google's stable account ID. Email addresses and IDs submitted by the browser cannot select another person's workspace. Existing local or shared workspace data is not imported automatically.

For Render deployment, see [Render hosting setup](docs/render-hosting.md). Personal workspaces require Google sign-in configuration, a persistent disk and a server encryption key. Legacy global worker tokens are disabled in personal-workspace mode until user-scoped worker authorization is implemented. Local development and password-protected private shared workspaces remain available when Google sign-in is not configured.

## Documentation

- [Setup and operations](docs/setup-and-operations.md)
- [Agent integration contract](docs/agent-config.md)

Observed daily loss starts at the first successful sync each UTC day. Known losses persist across reconnections within the same Demo or Live environment to prevent resetting the safety limit. This conservative protection can also constrain separate accounts.
