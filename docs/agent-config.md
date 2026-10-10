# Agent integration contract (version 1)

This branch owns account connections, agent configuration, user instructions and governance. The agentic teammate owns model calls and market selection. No AI worker or scheduler is started by the UI.

## Public agent contract (version 2)

Agent fields: `id`, `name`, `accountId` (connected Kalshi Demo/Live), `provider`, `model`, `modelValidated`, `modelKeyMask`, `status` (`draft`, `active`, `paused`, `deleted`), `personality` (`Conservative`, `Moderate`, `Aggressive`, or null before setup), `mandate` (null before setup), and `usage` (`daily`, `market`, `held`, `lastAmount`). There is no market/category field. The provider/model catalog lives exclusively in `static/providers.json`; saving validates the selected model through the provider's authenticated read-only API.

The mandate schema is:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["autoLimit", "dailyCap", "minimum", "newMarketApproval"],
  "properties": {
    "autoLimit": {"type": "number", "minimum": 0},
    "dailyCap": {"type": "number", "minimum": 0},
    "minimum": {"type": "number", "minimum": 0},
    "newMarketApproval": {"type": "boolean"}
  }
}
```

Example:

```json
{"id":"agent-uuid","name":"My trading agent","accountId":"account-uuid","provider":"openai","model":"gpt-4.1","modelValidated":true,"modelKeyMask":"••••1234","status":"active","personality":"Moderate","mandate":{"autoLimit":25,"dailyCap":100,"minimum":100,"newMarketApproval":true},"usage":{"daily":0,"market":0,"held":0,"lastAmount":null}}
```

New agents are drafts until the user saves a mandate. Only user UI actions mutate mandates or account ceilings. The worker receives neither policy mutation tools nor credential references. `autoLimit <= dailyCap <= effective account ceiling` is mandatory on save. Lowering the account ceiling later immediately governs future trades, including pending approvals.

Internal stored configs additionally contain `modelCredentialRef`, an opaque vault reference. Connected accounts have `credentialRef`. Neither reference nor plaintext credentials is returned to the browser/HTTP worker. The trusted local Python worker may load `Vault(DATA).get(reference)` using the same Keychain or master-key environment. Never give venue credentials or permission mutation tools to the language model. Do not log model keys.

## Worker HTTP boundary

Set `GOVERNTRADE_AGENT_TOKEN` to a random secret in both server and trusted worker environments. Use `Authorization: Bearer ...`. `GET /api/worker` returns public configurations and saved chat instructions. Consume only newly observed customer messages and persist your own cursor; do not repeatedly trade on historical messages. This token does not implement per-agent isolation; the trusted worker can submit for configured agents. Host is localhost only.

`POST /api/agent/proposals`:

```json
{
  "agentId": "273798cf-92d3-4bc5-ae4e-224a95cd8024",
  "marketId": "VENUE-MARKET-TICKER",
  "direction": "Yes",
  "amount": 10,
  "reasoning": "Explain the proposed trade for user review.",
  "idempotencyKey": "instruction-id:proposal-1"
}
```

Kalshi uses `Yes` or `No`. Amount is an all-in maximum USD budget. No leverage, closing/selling, transfers, withdrawals, scheduling or custom broker payloads are exposed. The account comes from the saved agent config, never from a model-selected account ID. The server retrieves the venue market/category, quote and account balance itself. The model cannot supply balances, prices, approvals, environment or rule overrides. Reuse an idempotency key on retries; changing the key makes a distinct proposal.

Response: `{"order": {...}}` with `decision` (`ALLOW`, `REQUIRE_APPROVAL`, `BLOCK`), status and evaluation history. A successful HTTP response is not a confirmed fill. The worker cannot approve blocked or pending orders using this endpoint. User decisions happen in the UI and re-evaluate current rules and account data. A price increase requires renewed approval.

`submission unknown` reserves the budget across restart. Do not resend with a fresh key. Confirm the venue result using the client order ID before implementing reconciliation. Kalshi IOC responses report immediate fill counts. Never assume a timeout is a rejection.

## Rules and balances

Every execution evaluates the agent's own mandate and the account-wide safety ceiling independently. `evaluations[].layers` reports `agent_mandate`, `account_ceiling` and `execution`; each check also names its layer. Approval can satisfy automatic-limit and new-market authorization, but never daily spending, minimum reserves, kill switch or execution hard blocks. The first successful fill, not a denied proposal, establishes market history for that agent.

Trading Permissions contains the shared daily spending ceiling and all-agent kill switch. Turning the switch off physically pauses active agents; turning it on does not silently resume them. Connection ceilings may be stricter. Existing hard market/exposure/slippage/loss protections remain in `engine.SAFETY`. Daily usage is UTC and conservatively counts authorized amounts. External Kalshi turnover and unresolved submissions are included. Observed loss is equity drawdown from the first daily sync, not a complete historical accounting ledger. Connections using different keys for the same underlying account are not automatically deduplicated.

Balance sources and subaccount reserve fallback are documented in README.md. All model/provider keys are validated on save using authenticated model retrieval, without running inference.

## Venue extension interface

Register a `VenueAdapter` subclass in `adapters.ADAPTERS`, with `venue`, `categories`, `test_connection()`, `get_markets(category)`, `get_quote(marketId,direction)`, `get_balance_positions()`, `place_order(governedOrder)`, `cancel_orders(ids)`. Fixed trusted API hosts only. Add credential fields/validation to account save and the frontend form when registering a venue. Only Kalshi is registered and exposed in the UI. New venues require explicit trusted implementation, never arbitrary API hosts.

Balances return `cash`, `positionValue`, `exposure`, `realizedPnl`, `unrealizedPnl`, `positions`, `asOf`, `source`, `mock`. Null means unavailable. Quotes contain `id`, `name`, `agent` (venue-derived category), `venue`, `instrument`, `price`, `quoteAt`, `closeAt`, `open`, `slippage`, `executionReady`, `executionNote`. Real adapter execution is called only from Workspace.evaluate after engine.checks allows the exact order.

Account safety controls also expose maximum account exposure (`exposureCap`), observed daily drawdown (`dailyLossCap`), protected available cash (`cashFloor`), and live trading enabled (`liveTrading`). These are hard gates across all agents on each bound account, never approval triggers. Workspace settings apply to every account; connection overrides only tighten them (minimum caps, maximum cash floor, AND for live trading). Existing execution safeguards remain enforced even if a configured cap is higher. Automatic trade authorization and new-market approval stay exclusively in each agent mandate. Turning live trading off blocks new real-money orders; it does not cancel existing orders or liquidate positions.
