# Agent integration contract (version 1)

This branch owns account connections, agent configuration, user instructions and governance. The agentic teammate owns model calls and market selection. No AI worker or scheduler is started by the UI.

## Public agent JSON Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "GovernTradeAgent",
  "type": "object",
  "required": ["id", "name", "category", "accountId", "provider", "model", "status", "permissions"],
  "properties": {
    "id": {"type": "string", "format": "uuid"},
    "name": {"type": "string", "minLength": 1, "maxLength": 100},
    "category": {"enum": ["crypto", "sports", "economics", "politics", "culture", "other"]},
    "accountId": {"type": "string", "format": "uuid"},
    "provider": {"enum": ["openai", "anthropic", "google", "other"]},
    "model": {"type": "string", "minLength": 1, "maxLength": 100},
    "status": {"enum": ["active", "paused", "deleted"]},
    "modelKeyMask": {"type": "string"},
    "permissions": {"$ref": "#/$defs/rules"},
    "effectiveRules": {"$ref": "#/$defs/rules"},
    "usage": {"type": "object", "properties": {"daily": {"type": "number"}, "market": {"type": "number"}, "held": {"type": "number"}}}
  },
  "$defs": {
    "rules": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "autoLimit": {"type": "number", "minimum": 0},
        "dailyCap": {"type": "number", "minimum": 0},
        "dailyLossCap": {"type": "number", "minimum": 0},
        "marketCap": {"type": "number", "minimum": 0},
        "exposureCap": {"type": "number", "minimum": 0},
        "minimum": {"type": "number", "minimum": 0},
        "slippage": {"type": "number", "minimum": 0},
        "automation": {"type": "boolean"},
        "alwaysApprove": {"type": "boolean"},
        "newMarketApproval": {"type": "boolean"},
        "notifications": {"type": "boolean"},
        "categories": {"type": "array", "items": {"enum": ["crypto", "sports", "economics", "politics", "culture", "other"]}},
        "approvedMarkets": {"type": "array", "items": {"type": "string"}}
      }
    }
  }
}
```

Example (IDs illustrative):

```json
{
  "id": "273798cf-92d3-4bc5-ae4e-224a95cd8024",
  "name": "My BTC agent",
  "category": "crypto",
  "accountId": "c6325071-602c-4200-aa04-a1038ba6819d",
  "provider": "openai",
  "model": "your-chosen-model-id",
  "modelKeyMask": "••••1234",
  "status": "active",
  "permissions": {"dailyCap": 30, "autoLimit": 10},
  "effectiveRules": {"dailyCap": 30, "autoLimit": 10, "dailyLossCap": 50, "marketCap": 50, "exposureCap": 500, "minimum": 100, "slippage": 1, "automation": true, "alwaysApprove": false, "newMarketApproval": false, "notifications": true, "categories": ["crypto"], "approvedMarkets": []},
  "usage": {"daily": 0, "market": 0, "held": 0}
}
```

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

Coinbase spot direction is `Buy`; Kalshi uses `Yes` or `No`. Amount is an all-in maximum USD budget. No leverage, closing/selling, transfers, withdrawals, scheduling or custom broker payloads are exposed. The account comes from the saved agent config, never from a model-selected account ID. The server retrieves the venue market/category, quote and account balance itself. The model cannot supply balances, prices, approvals, environment or rule overrides. Reuse an idempotency key on retries; changing the key makes a distinct proposal.

Response: `{"order": {...}}` with `decision` (`ALLOW`, `REQUIRE_APPROVAL`, `BLOCK`), status and evaluation history. A successful HTTP response is not a confirmed fill. The worker cannot approve blocked or pending orders using this endpoint. User decisions happen in the UI and re-evaluate current rules and account data. A price increase requires renewed approval.

`submission unknown` reserves the budget across restart. Do not resend with a fresh key. Confirm the venue result using the client order ID before implementing reconciliation. Coinbase submitted orders reconcile by venue order ID during refresh. Kalshi IOC responses report immediate fill counts. Never assume a timeout is a rejection.

## Rules and balances

Global defaults, account overrides and agent overrides combine strictly: smallest budget/threshold/exposure/slippage cap, largest cash reserve, intersection of allowed categories/approved markets, OR for required approval, AND for automation. Market caps accumulate across agents bound to the account. Daily budgets use UTC and conservatively count the authorized budget, even when an IOC fills less. Unresolved submissions additionally reserve available cash and exposure.

Daily loss is **observed equity drawdown since the first successful sync that UTC day**, not the venue's full-day realized P/L. Withdrawals count as drawdown; earlier losses before that sync are not known. Do not market this as complete daily accounting. Kalshi and Coinbase external fill turnover is included conservatively in shared daily spending. Separate API connections to the same underlying account must not be treated as distinct funding sources; automatic identity deduplication is a TODO.

## Venue extension interface

Register a `VenueAdapter` subclass in `adapters.ADAPTERS`, with `venue`, `categories`, `test_connection()`, `get_markets(category)`, `get_quote(marketId,direction)`, `get_balance_positions()`, `place_order(governedOrder)`, `cancel_orders(ids)`. Fixed trusted API hosts only. Add credential fields/validation to account save and the frontend form when registering a venue. "Other venue" does not accept arbitrary URLs or send secrets to unregistered adapters.

Balances return `cash`, `positionValue`, `exposure`, `realizedPnl`, `unrealizedPnl`, `positions`, `asOf`, `source`, `mock`. Null means unavailable. Quotes contain `id`, `name`, `agent` (venue-derived category), `venue`, `instrument`, `price`, `quoteAt`, `closeAt`, `open`, `slippage`, `executionReady`, `executionNote`. Real adapter execution is called only from Workspace.evaluate after engine.checks allows the exact order.
