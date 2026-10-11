# Trading rules

Every risk limit in a workspace is set by its owner, on sliders. Every money rule is a percentage of the account's
current value (cash, reserved cash and marked positions), and each slider shows live what it means for the user's
own account ("At most $25.00 of your $500.00 account at risk in one market"). Each rule starts at a suggested
position; a workspace still cannot trade until its owner has reviewed and saved them on the **Set your trading
rules** screen, which appears right after sign-in. Rules can be changed at any time under **Trading Rules**.

[`trading_rules.py`](../trading_rules.py) is the single source of truth: each rule's name, unit, range, slider step
and scale, starting position and explanation. The sliders, their previews and the server-side validation all read
it. Each slider has a number box for exact values; ranges spanning orders of magnitude (proposal lifetime,
approval window, how fast trust changes) slide on a log scale so small values stay easy to reach.

## The rules (schema 2)

| Group | Rule | Unit | Range | Starts at | Used by |
| --- | --- | --- | --- | --- | --- |
| Bet sizing | Kelly multiplier | % | 1–100 | 25 | Governor sizing (fraction of full Kelly) |
| Exposure caps | Per-market cap | % of account | 0–100 | 5 | Governor and order checks |
| | Per-agent cap | % of account | 0–100 | 15 | Governor |
| | Per-event cap | % of account | 0–100 | 10 | Governor |
| | Total exposure cap | % of account | 0–100 | 30 | Governor and order checks |
| Daily limits | Daily spending ceiling | % of account | 0–100 | 20 | Order checks; caps every agent mandate |
| | Daily loss limit | % below day open | 0.1–100 | 3 | Governor breaker (latches until reset) and order checks |
| Timing | Price freshness | seconds | 1–600 | 30 | Governor order books and order-time quotes |
| | Proposal lifetime | seconds | 1–3600 | 60 | Default proposal expiry |
| | Approval window | seconds | 60–86400 | 900 | How long a governed order waits for approval |
| Trust | Starting trust | % | 1–99 | 50 | Reliability prior for a new agent |
| | How fast trust changes | forecasts | 1–1000 | 20 | Reliability prior weight |

Each agent's mandate uses the same sliders, as shares of its account's value: automatic trade limit (starts at
2%), agent daily cap (10%), minimum cash reserve (20%) and new-market approval (off). The agent daily cap must fit
the daily spending ceiling, and the automatic limit must fit the agent daily cap. A connected account can have a
lower daily ceiling of its own, also a percentage.

Always on, and not configurable because they protect data and order integrity: the market must be open, binary
markets only, $1 minimum order, a fresh account balance, enough cash, and live-money confirmation.

## How the rules are applied

- Percentages become dollars at the moment of each check, from the account's current value. The order checks use
  the balance read from Kalshi just before the order; the governor uses its batch's account value. The daily
  loss limit is the exception by definition: it is a drop from the start-of-day value, or after a same-day breaker
  reset from the lower of that and the value at reset, at both the governor and the order checks.
- The governor builds its policy from the workspace's rules on every batch (`trading_rules.to_policy`). Each save
  that changes a value increments the version, and every decision records it (`policyVersion`, e.g.
  `user-rules-v3`). Saving unchanged values creates no version.
- Without saved rules, a "Trading rules set" check blocks every order, the governed API refuses proposals, the chat
  agent cannot propose, and mandates cannot be saved or agents resumed.
- `GovernorService(policy=...)` still accepts a fixed policy for replays and tests.

## Upgrades

- **Schema 1 (dollar rules, PR #36):** converted once to percentages from the account values when the workspace is
  next loaded, then shown for review ("Review your trading rules"); nothing trades until the owner saves them. Where
  a dollar limit and a percentage cap became one rule (per-market, total exposure, daily loss), the stricter of the
  two is kept. Without a known account value, the dollar rules start at their suggested positions. The rules
  version continues, so decision history stays in order.
- **Dollar mandates and per-account ceilings** become the same share of their account's value. A ceiling that was
  only the old workspace cap copied onto the account is dropped. A mandate whose account has no known value is
  cleared, so its owner sets it again.
- **Workspaces from before user-set rules** see the suggested positions.
- **Legacy Postgres mode:** rules live in `workspace_settings.extra`, and percentage mandates in `agents.extra`
  (their dollar columns are written as null). The `daily_cap`, `daily_loss_cap`, `market_cap`, `exposure_cap` and
  `slippage_max` columns are no longer read or written.

The presenter demo (**▶ DEMO**) is a scripted simulation and keeps its own fixed dollar limits and agent presets.

## Removed: the slippage limit

The old fixed slippage check compared each quote's reported slippage with a limit of 1. Kalshi quotes always
report a slippage of 0, so the check never did anything, and it was removed rather than offered as a rule.

Implications for later work:

- Kalshi orders are immediate-or-cancel limit orders at the governor's limit price, so an order can never fill
  above the price it was sized at. Slippage is bounded by the limit price, not by a separate rule.
- A venue that fills market orders, or an order type without a hard price limit, would need a real slippage rule.
  Add it to `trading_rules.RULES` (for example "Maximum slippage", in cents per contract) and check it where that
  venue's quote reports slippage, so the user sets it like every other rule.
- `adapters.KalshiAdapter.get_quote` and `replay/paper_venue.py` still return `slippage: 0`; that field is unused.
