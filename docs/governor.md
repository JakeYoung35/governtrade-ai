# Portfolio governor: operations guide

The governor sits between AI agents and Kalshi. Agents propose trades; the governor sizes, ranks and
caps them across one shared bankroll, then sends approved stakes through the app's existing mandate,
account ceiling and approval checks. Every decision is recorded with the inputs needed to reproduce it.

The worker-facing proposal contract is documented in [agent-config.md](agent-config.md) and served live
at `GET /api/governor/schema`. This page covers how decisions are made and how to operate the governor.

## How a proposal is decided

1. **Submit.** `POST /api/governor/proposals` validates the JSON strictly (field-level errors), checks the
   agent is active with a mandate, records the agent's forecast, and queues the proposal (`202`).
2. **Batch.** Every `GOVERNOR_BATCH_SECONDS` (default 5) the server decides all queued proposals, one batch
   per connected account:
   - refreshes the account balance and reads Kalshi positions (attributed to agents through their fills);
   - fetches the order book of every proposed market;
   - checks the daily loss breaker;
   - ranks proposals by reliability x net edge and sizes each with fractional Kelly, then cuts it to the
     tightest of cash and the market, agent, event and total caps, walking the order book with exact fees.
3. **Execute.** Approved stakes go through `Workspace.proposal`, so the user's mandate (automatic limit,
   daily cap, cash reserve), the account ceiling and the app's safety checks still apply, including
   requests for user approval. The order buys exactly the approved contracts as an immediate-or-cancel order
   at the governor's limit price, so if the market has moved above that limit the order simply goes unfilled
   (status `cancelled`). A stake under the app's $1 minimum is skipped with a reason. A governed order that
   needs the user's approval can be approved for `approval_ttl_seconds` (15 minutes by default); after that it
   stops counting toward exposure, and a late approval cancels it instead of trading on stale sizing.
4. **Look up.** `GET /api/governor/proposals/{governorProposalId}` returns the status, the decision (stake,
   contracts, limit, reason codes, reliability, policy version) and the downstream order.

Only one proposal per market wins a batch: an opposite-side proposal is rejected as `conflict`, a same-side
one as `redundantSignal`. A market with an open position is rejected as `positionAlreadyOpen`.

## Policy defaults (`governor/policy.py`, version `governor-policy-v1`)

| Setting | Default | Meaning |
| --- | --- | --- |
| Kelly fraction | 0.25 | Quarter Kelly before caps |
| Market cap | 5% of equity | Loss-at-risk in one market |
| Agent cap | 15% | Loss-at-risk per agent |
| Event-group cap | 10% | Related markets of one event (e.g. both teams of a game) |
| Total cap | 30% | All open loss-at-risk |
| Daily loss limit | 3% | Breaker latches at this decline from the day's base |
| Quote freshness | 30 s | Older order books are rejected as `staleQuote` |
| Proposal lifetime | 60 s | Unless the proposal sets `expiresAt` (at most one hour) |
| Reliability prior | 0.5, weight 20 | New agents start at 0.5; resolved forecasts move it gradually |

Caps are fractions of the account's current equity (cash + reserved + marked positions), so a small demo
account produces small stakes: at about $100 of equity the market cap is about $5.

## Daily loss breaker

The breaker latches when equity falls 3% below the day's base, which is the account's first balance sync of
the UTC day (unrealized losses count). While latched, every proposal is rejected as `lossBreakerLatched`.
It stays latched through recoveries and into the next day; only an operator reset clears it:

```json
POST /api/action  {"action": "governor-reset-breaker", "accountId": "...", "reason": "Reviewed the loss"}
```

The reset is recorded permanently (table `governor_breaker_events`) and, for the rest of that day, the limit
is measured from the lower of the day open and the equity at reset.

## Reliability scoring

About once a minute the server checks each market that has unscored forecasts. When Kalshi settles it, the
forecast is scored (Brier) and the agent's reliability updates, which changes its future Kelly stakes. Every
agent's first forecast per market is scored, including proposals the governor rejected, so reliability does
not depend on the governor's own decisions. Run it on demand with `{"action": "governor-score"}`.

## Failure handling and locking

- Every request's outcome is recorded before any order is sent, and each batch first finishes requests left
  `approved` (for example after a crash). The order's idempotency key is the governor proposal id, so this
  never places an order twice; proposals that expired meanwhile are skipped.
- One account's failure never stops another's. A request that fails to be evaluated is retried on the next
  pass and rejected after three failed attempts. An execution failure marks only that request
  (`executionFailed`) and links any order already created.
- Each pass plans under the workspace lock, reads balances, order books and market outcomes with the lock
  released, then decides and saves under the lock. Order placement goes through `Workspace.proposal`, which,
  like the app's existing order route, refreshes the balance and quotes while holding the lock. In Postgres
  mode an idle pass costs one small query instead of a full workspace load.
- While the lock is released, other requests can change the account. If the account was refreshed under the
  lock after the governor's read started (as `Workspace.evaluate` does after every order), the decision uses
  that newer balance, also when the governor's own read failed, rather than rolling it back. Each order records
  when the venue answered (`executedAt`). Filled or committed orders answered at or after the start of the
  balance read actually used count as exposure on top of its positions; orders answered earlier are already in
  them. A fill that lands while the unlocked read is running may be counted twice for that one pass, which
  only makes stakes smaller.

## Configuration

| Variable | Purpose |
| --- | --- |
| `GOVERNTRADE_AGENT_TOKEN` | Bearer token for the worker endpoints (`/api/governor/*` except the summary) |
| `GOVERNOR_BATCH_SECONDS` | Batch interval in seconds; `0` disables the background loop (batches then run only via `governor-run-batch`) |
| `DATABASE_URL` | Optional Postgres; governor tables are created by `migrations/003_governor.sql` and `004_governor_requests.sql` |

`GET /api/governor/summary` (same-origin, like `/api/state`) returns the queue size, breaker states and
recent breaker events, agent reliability, and recent decisions and requests for a dashboard.

## Storage and audit

| Table | Contents |
| --- | --- |
| `governor_batches` | Every input of a batch (policy, portfolio, reliability, proposals, raw order books) |
| `governor_decisions` | One decision per proposal, with its full trace |
| `governor_forecasts` | The first forecast per agent and market, and its outcome |
| `governor_breaker_events` | Every breaker latch and reset |
| `governor_requests` | Each submitted proposal and its current status |

`governor.records.replay(batch)` recomputes a stored batch's decisions exactly. Without `DATABASE_URL` the same
records live in `.data/main.json`, bounded to recent history.

## Offline replay demo

```sh
python3 scripts/replay_demo.py           # print every beat
python3 scripts/replay_demo.py --pause   # wait for Enter between beats while presenting
```

Runs the real governor and app checks against a paper venue over recorded Kalshi demo order books, with no
network. It shows one batch with an approval, a conflict and a cap resize; an identical retry that does not
trade twice; a price crash that latches the breaker; an audited reset; and settlement scoring that changes
reliability and the next stake. All balances and fills in the demo are simulated.

## Known limits

- One position per market: a second proposal on a market with an open position is rejected.
- Each order book costs four Kalshi requests (market, event, series, book); large batches may approach the
  30-second freshness window.
- Every stored forecast is loaded on each request; fine for now, bounded loading is a follow-up.
- A retry of any earlier `proposalId` returns the original record: Postgres looks older requests up in
  `governor_requests`, and the JSON file keeps them in slim form. Use a new `proposalId` for each new proposal.
