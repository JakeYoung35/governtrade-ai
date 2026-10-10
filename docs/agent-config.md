# Agent integration contract

The app owns account connections, agent configuration, user instructions and governance, including the portfolio governor that sizes and caps agent proposals. The agentic teammate's worker owns model calls and market selection. The UI starts no AI worker; the server runs only the governor's batch loop.

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

### Governed proposal contract (schema version 1)

Adopted 2026-10-10. [`governor/api.py`](../governor/api.py) is the source of truth for the proposal schema, validation, example, and response contract. This supersedes the older proposed “contract v3”; the public agent configuration version above is separate from proposal `schemaVersion`.

Use the worker bearer token for schema discovery, submission, and lookup:

- `GET /api/governor/schema`
- `POST /api/governor/proposals`
- `GET /api/governor/proposals/{governorProposalId}`

```json
{
  "schemaVersion": 1,
  "proposalId": "instruction-id:proposal-1",
  "agentId": "273798cf-92d3-4bc5-ae4e-224a95cd8024",
  "marketId": "VENUE-MARKET-TICKER",
  "side": "yes",
  "estimatedYesProbability": 0.45,
  "maxBudgetUsd": 10,
  "maxEntryPrice": 0.40,
  "reasoning": "Explain the proposed trade for user review."
}
```

All example fields except `maxEntryPrice` are required. `expiresAt` is also optional. Unknown fields are rejected. Send numeric fields as finite JSON numbers, not strings or booleans.

- `side`: lowercase `yes` or `no`.
- `estimatedYesProbability`: 0.01-0.99 inclusive; always the probability of YES, including for a NO purchase. It is scored against the settled result (Brier score), and the agent's reliability from those scores scales its future stakes. Only the agent's first forecast per market counts, including proposals the governor rejected, so report honest probabilities.
- `maxBudgetUsd`: $1-$1,000,000 inclusive, the maximum all-in budget including fees. The governor can shrink or reject it.
- `maxEntryPrice`: optional 0.01-0.99 inclusive, in dollars per contract for the chosen side. Without it, the governor still buys only at prices with positive expected edge, and the order is sent as immediate-or-cancel at the governor's own limit price.
- `expiresAt`: optional future ISO-8601 timestamp with timezone, at most one hour after receipt. Default lifetime is the policy value, currently 60 seconds.
- `proposalId`: client-generated retry identifier, scoped to the agent, 1-200 letters, digits, dots, underscores, colons, or hyphens. Reuse the same ID and payload on retries.
- `reasoning`: nonblank text, at most 8,000 characters. Agent and market IDs must be nonblank strings of at most 200 characters.

New submission response: HTTP `202`, with `governorProposalId`, the client `proposalId`, and `status: "queued"`. Persist the server-generated `governorProposalId` and use it for lookup; it is distinct from the client retry ID and any venue order ID.

An identical retry returns HTTP `200` and the existing record without creating another proposal, also after the agent was paused and for older proposals (looked up in the database, or kept in slim form in the local JSON file). Reusing the same agent/proposal ID with a changed payload returns `409`. Invalid submissions return `400` with field-level `errors` and a `schema` link. Unknown lookup IDs return `404`. Keep the retry payload unchanged, including any explicit expiry; a timeout is not a rejection.

Poll lookup for the governor decision and downstream order status. Request `status` is `queued` (waiting for the next batch, every few seconds), `rejected` (the governor rejected it, see `decision.reasonCodes`, or it could not be evaluated, see `note`), `approved` (approved or resized; the order is being placed), `submitted` (an order was created; see `order.status`, which can still be `awaiting approval`, `blocked`, `filled`, `cancelled` or `submission unknown`), `skipped` (approved, but the stake was under the app's $1 minimum, or the proposal expired before an interrupted execution could resume; see `note`) or `executionFailed` (see `note`; an order created before the failure is linked). An order is an immediate-or-cancel at the governor's limit price, so a market that moved above the limit leaves it unfilled (`order.status` `cancelled`). An order awaiting the user's approval can be approved for 15 minutes; a later approval cancels it. Queue acceptance and governor approval do not confirm execution or a fill. Approved or resized stakes still pass the user's mandate and effective account ceiling; human approval may be required. The worker cannot approve trades, reset the breaker, or change policy.

The account comes from the saved agent configuration. The server obtains market metadata, order books, balances, and execution quotes. The worker supplies a price limit, not a trusted quote, and cannot supply account IDs, balances, approvals, environment, credentials, or rule overrides. No selling, leverage, transfers, withdrawals, or arbitrary broker payloads are exposed.

`submission unknown` must retain its reservation across restart. Do not retry with a fresh proposal ID; reconcile the venue result using the execution client order ID. Human approval rechecks current rules and account data; a price increase requires renewed approval.

### Migration and implementation status

The governed route is implemented (see [governor.md](governor.md) for how batches, caps, the loss breaker and scoring work) and was verified end to end on 2026-10-10 against the Kalshi demo exchange (`external-api.demo.kalshi.co`): a proposal submitted through `GovernorService` was approved for 4 contracts at $0.53 and all 4 filled through `Workspace` and `KalshiAdapter`. The offline equivalents are `scripts/replay_demo.py` and `test_governor_service.py`. Check `GET /api/governor/schema` on the running revision before switching a worker.

The legacy `/api/agent/proposals` route uses `direction`, `amount`, and `idempotencyKey`, and returns an immediate `order`. New workers must use the governor route. There is no automatic compatibility mapping: use `side`, `maxBudgetUsd`, and `proposalId`, and additionally provide `schemaVersion` and `estimatedYesProbability`. Do not fall back to the legacy route on governor errors. The legacy route still exists and bypasses the governor's sizing, conflict and breaker checks (it keeps the mandate and ceiling checks); retire or restrict it once workers have migrated.

## Rules and balances

Every execution evaluates the agent's own mandate and the account-wide safety ceiling independently. `evaluations[].layers` reports `agent_mandate`, `account_ceiling` and `execution`; each check also names its layer. Approval can satisfy automatic-limit and new-market authorization, but never daily spending, minimum reserves, kill switch or execution hard blocks. The first successful fill, not a denied proposal, establishes market history for that agent.

Trading Permissions contains the shared daily spending ceiling and all-agent kill switch. Turning the switch off physically pauses active agents; turning it on does not silently resume them. Connection ceilings may be stricter. Existing hard market/exposure/slippage/loss protections remain in `engine.SAFETY`. Daily usage is UTC and conservatively counts authorized amounts. External Kalshi turnover and unresolved submissions are included. Observed loss is equity drawdown from the first daily sync, not a complete historical accounting ledger. Connections using different keys for the same underlying account are not automatically deduplicated.

Balance sources and subaccount reserve fallback are documented in README.md. All model/provider keys are validated on save using authenticated model retrieval, without running inference.

## Venue extension interface

Register a `VenueAdapter` subclass in `adapters.ADAPTERS`, with `venue`, `categories`, `test_connection()`, `get_markets(category)`, `get_quote(marketId,direction)`, `get_orderbook(marketId)`, `get_balance_positions()`, `place_order(governedOrder)`, `cancel_orders(ids)`. Fixed trusted API hosts only. Add credential fields/validation to account save and the frontend form when registering a venue. Only Kalshi is registered and exposed in the UI. New venues require explicit trusted implementation, never arbitrary API hosts.

Balances return `cash`, `positionValue`, `exposure`, `realizedPnl`, `unrealizedPnl`, `positions`, `asOf`, `source`, `mock`. Null means unavailable. Quotes contain `id`, `name`, `agent` (venue-derived category), `venue`, `instrument`, `price`, `quoteAt`, `closeAt`, `open`, `slippage`, `executionReady`, `executionNote`. Order books, read by the governor (`governor/snapshot.py`) to size against full depth, contain `id`, `name`, `eventTicker`, `agent`, `venue`, `open`, `executionReady`, `feeMultiplier`, `closeAt`, `yesBids` and `noBids` (`[price, quantity]` decimal strings, bids for each outcome; asks are derived as one minus the opposing bid) and `asOf`. Real adapter execution is called only from Workspace.evaluate after engine.checks allows the exact order.
