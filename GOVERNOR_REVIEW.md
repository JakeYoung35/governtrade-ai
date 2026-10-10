# Governor review and build plan

Reviewed 2026-10-09 against the current source and the parent workspace's
`governtrade-mvp 1.md`. This document proposes implementation work; it does not
change trading behavior.

## Assessment

The current engine is a useful permissions gate and deterministic local demo.
It enforces cash reserves, spending caps, agent/category/venue permissions,
quote age, explicit approvals, and mandate limits. Approval reevaluates current
rules; settlement continues independently of permission to open new trades.

It is not yet the shared-capital allocator described in the brief. There are no
probability-bearing proposals, reliability history, batch ranking, resizing,
event-group limits, executable bid marking, or latched daily loss breaker.
The five agent cards represent categories, not independently scored strategies.

Retain the current dashboard and paper simulation while developing a separate,
testable governor module. A frontend rewrite is unnecessary for the first
complete proposal-to-decision-to-ledger demonstration.

## Findings to fix first

All five findings below were reproduced with fresh in-memory state. Priorities
reflect their effect on correctness, not an assertion of public exposure.

| Priority | Location | Finding and effect | Required correction |
| --- | --- | --- | --- |
| High | `engine.py:106` | `Invest $10 in BTC 15-minute Up/Down, No.` buys YES because `up` in the market title wins the direction match. | Parse the market separately from the instruction; honor an unambiguous explicit side and clarify contradictory instructions. |
| High | `engine.py:25` | Daily spend uses proposal time. A $40 proposal at 23:59 approved at 00:01, followed by another $40 approval, fills $80 against a $40 daily cap. | Persist execution timestamps and aggregate fills by UTC execution day; account for reservations in asynchronous execution. |
| High | `engine.py:124` | An approval payload with `approve: "false"` is truthy and fills a pending order. | Require an actual boolean; authenticate the operator before cloud deployment. |
| Medium | `engine.py:86` | Numeric permissions accept infinity and NaN; infinity can remove a cap. | Require finite, bounded values and reject nonstandard JSON numeric constants. Use exact financial decimal validation in new models. |
| Medium | `engine.py:57` | Reusing a key for a different amount/side silently returns the original order. Keys also share a namespace with deposits. | Store a normalized payload digest with a scoped key; return a conflict for changed payloads and isolate action namespaces. |

Additional design gaps found by inspection:

- `checks()` does not independently enforce registered agent/market ownership;
  the structured trade route alone performs the market-agent match. Put this
  invariant in the shared governor and use it for preview as well as execution.
- The freshness check permits future-dated quotes because it only checks an
  upper age bound. Use timezone-aware UTC and reject negative ages beyond a
  documented clock-skew tolerance.
- Positions retain initial values until settlement. Cost-based exposure is not
  a current valuation or a basis for a mark-to-market daily loss breaker.
- Decisions retain human-readable checks, but no immutable policy version,
  portfolio snapshot, reliability snapshot, or allocator version.
- The HTTP lock and atomic file replacement suit one local process. They do
  not supply authenticated operator/agent separation or database transactions.

## Research that affects implementation

Kalshi documents public market-data endpoints without authentication. A first
integration can therefore ingest curated markets without adding order placement
or custody. Keep recorded replay inputs available when the API is unavailable.
[Official market-data quick start](https://docs.kalshi.com/getting_started/quick_start_market_data).

The current order book uses `orderbook_fp.yes_dollars` and `no_dollars`, with
price and quantity represented as strings. Convert them to Decimal at the
adapter boundary. The YES ask is one minus the best NO bid, and vice versa;
retain the corresponding depth. Missing opposite-side bids mean no executable
ask, not a fabricated 0.50 price. Support instrument quantity increments rather
than assuming the venue only allows whole contracts.
[Official order-book reference](https://docs.kalshi.com/getting_started/orderbook_responses).

For cloud persistence, my recommendation is one locked portfolio row per
transaction. PostgreSQL `SELECT ... FOR UPDATE` prevents conflicting writers
and lockers from changing that row until transaction end. Require every capital
mutation to acquire that lock, including fills, reservations, resets, and
settlement. Unique constraints remain necessary for proposal and fill identity.
[PostgreSQL locking documentation](https://www.postgresql.org/docs/current/explicit-locking.html).

If adding Pydantic, use strict boolean validation and finite, bounded numeric
fields. Its documented finite-float type rejects infinity and NaN; decimal
financial models should apply equivalent finite constraints. Merely adding a
schema library will not establish identity authorization or ledger atomicity.
[Pydantic types reference](https://docs.pydantic.dev/latest/api/types/).

## Proposed modules and contracts

Start as a Python package alongside `engine.py`, keeping HTTP and persistence
outside allocation calculations:

| Module | Responsibility |
| --- | --- |
| `governor/models.py` | Proposal, policy, market/portfolio snapshots, decision, reservation and fill contracts. |
| `governor/validation.py` | Identity bindings, finite inputs, allowlists, expiry, quote and valuation freshness. |
| `governor/scoring.py` | Resolved-forecast Brier score, sample count, and versioned reliability heuristic. |
| `governor/allocation.py` | Pure fractional-Kelly sizing, stable batch ranking, conflicts and exposure headroom. |
| `governor/risk.py` | Pause state, hard caps, daily loss latch, and operator reset records. |
| `governor/execution.py` | Paper fills, fee calculation, liquidity consumption and reservation release. |
| `governor/ledger.py` | Cash movements, positions, marks, settlement and reconciliation. |
| `governor/service.py` | Transaction boundary, idempotency, current-policy checks and audit persistence. |

Use the brief's proposal fields: `proposalId`, `agentId`, `marketId`, `side`,
`estimatedYesProbability`, `maxEntryPrice`, `requestedBudgetUsd`, `expiresAt`,
`snapshotId`, and optional rationale. Bind agent identity from authentication,
not just the submitted field. Neither reliability nor policy comes from agents.

A decision should include requested/target/approved budgets, approved quantity,
estimated and actual fees, reason codes, per-rule headroom, policy version,
market and portfolio snapshot IDs, reliability and its sample count, evaluation
time, allocator version, and explicit paper/replay mode. Keep allocation outcome
separate from approval and execution status.

## Allocation sequence

1. Validate proposals and freeze batch membership, time, policy, portfolio,
   market inputs, and server-computed reliability. Reject stale valuation and
   paused portfolios before permitting new risk.
2. Convert a NO forecast to `1 - estimatedYesProbability`. With selected-side
   probability `p` and buffered entry cost `c`, the brief proposes
   `max(0, (p-c)/(1-c))` as full Kelly. Require `0 < c < 1` and positive net edge.
3. Multiply by equity, configured fractional Kelly, and reliability, capped by
   the requested all-in budget. These are proposed heuristics from the brief,
   not evidence of trading profitability.
4. Rank eligible proposals by reliability times net edge, descending, then
   proposal ID. Select at most one funded proposal per market. Define a
   zero-fill top candidate as not consuming the winner slot; consider the next
   eligible candidate deterministically. Reject conflicting and redundant
   proposals once a winner exists; do not add to an open market position.
5. Apply remaining market, agent, related-event, and portfolio headroom plus
   available cash, reserve, daily-spend, mandate and customer permissions.
   Pending execution reservations count against all relevant headroom.
6. Round quantity down to the supported increment. Recompute actual modeled
   fees, price-ceiling compliance, depth, and all-in cost; reduce again if
   necessary. Never count the sizing buffer a second time as a ledger fee.
7. Recheck mutable execution gates and persist decisions, fills, and ledger
   effects atomically. A fresh price outside approved terms requires a new
   decision. External data fetching occurs outside the database lock.

Use the brief's 5% market, 15% agent, 10% event-group, 30% total exposure and 3%
daily loss thresholds as configurable proposed defaults. Keep these distinct
from the existing simulator's dollar spending limits. Define exposure as
committed entry capital including fees and unfilled reservations for this MVP;
use executable exit marks for equity and P&L. Avoid double-counting a reservation
after it becomes a fill. Use a fixed equity denominator within a batch.

For daily loss, persist the UTC day-opening equity and adjust for external cash
flows so deposits do not conceal losses. Latch the breaker until an authenticated
reset with a reason. Continue marks and settlements while paused. Stale marks
block new commitments and cannot silently clear the breaker.

## Delivery order and acceptance checks

1. **Harden the existing engine.** Fix the five reproduced issues and add
   regression coverage for explicit NO, midnight approvals, invalid booleans,
   nonfinite inputs, and changed-payload retries. Preserve the current 17 tests.
2. **Build the pure allocator against fixed replay inputs.** Cover YES/NO
   symmetry, zero edge, Kelly arithmetic, stable ranking under shuffled input,
   resizing, same-market conflicts, reservations, and rounding boundaries.
3. **Complete the paper ledger and breaker.** Add partial fills, depth limits,
   bid-side marking and settlement. Verify cash plus positions equals equity,
   fee/P&L reconciliation, no repeated settlement, and pause/reset semantics.
4. **Add durable transactions and authenticated proposal/control APIs.** Test
   concurrent proposals against one budget and restart/retry recovery. A failed
   write must leave no fill or capital mutation committed. Keep the existing
   dashboard while exposing richer decisions through its API.
5. **Add read-only market ingestion and three strategy agents.** Treat strategy
   identity and market category as separate fields. Score one predeclared
   forecast per agent/market after verified resolution, including rejected
   trades, to avoid scoring only the forecasts selected for execution.
6. **Finish the replay demo.** Show allocation, resizing, conflict rejection,
   idempotent retry, a marked loss that latches pause, explicit reset, settlement,
   and a reliability update. The full sequence must work offline.

## Verification performed

- Existing backend suite: 17 tests passed.
- `node --check static/app.js`: passed.
- Five targeted in-memory reproductions confirmed the findings above.
- Read the backend, adapter, dashboard integration, project documentation and
  MVP brief; checked official references linked above.
- No live venue calls, browser verification, cloud deployment or application
  behavior changes were performed. Existing tests write ignored local test data.
