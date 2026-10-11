# Trading rules

Every risk limit in a workspace is set by its owner. There are no preset values: a new workspace cannot trade
until its owner has saved every rule on the **Set your trading rules** screen, which appears right after sign-in
and blocks the rest of the app until it is saved. Rules can be changed at any time under **Trading Rules**.

[`trading_rules.py`](../trading_rules.py) is the single source of truth. It holds each rule's name, unit, valid
range, explanation and example; the setup screen, the Trading Rules page and the server-side validation all read
it, so the explanation a user sees is always the rule that is enforced.

## The rules

| Group | Rule | Unit | Range | Used by |
| --- | --- | --- | --- | --- |
| Bet sizing | Kelly multiplier | % | 1–100 | Governor sizing (fraction of full Kelly) |
| Exposure caps | Per-market cap | % of equity | 0–100 | Governor |
| | Per-agent cap | % of equity | 0–100 | Governor |
| | Per-event cap | % of equity | 0–100 | Governor |
| | Total exposure cap | % of equity | 0–100 | Governor |
| Dollar limits | Daily spending ceiling | $ | ≥ 0 | Order checks; caps every agent mandate |
| | Per-market dollar limit | $ | ≥ 0 | Order checks |
| | Open exposure dollar limit | $ | ≥ 0 | Order checks |
| Loss protection | Daily loss dollar limit | $ | ≥ 0 | Order checks |
| | Loss breaker | % below day open | 0.1–100 | Governor breaker (latches until reset) |
| Timing | Price freshness | seconds | 1–600 | Governor order books and order-time quotes |
| | Proposal lifetime | seconds | 1–3600 | Default proposal expiry |
| | Approval window | seconds | 60–86400 | How long a governed order waits for approval |
| Trust | Starting trust | % | 1–99 | Reliability prior for a new agent |
| | How fast trust changes | forecasts | 1–1000 | Reliability prior weight |

A cap of 0% stops new risk of that kind. Each agent also has its own mandate (automatic trade limit, daily cap,
minimum cash reserve, new-market approval), set when the agent is created, again with no presets. Mandates can
only be saved once the workspace rules exist, and an agent's daily cap must fit the daily spending ceiling.

Always on, and not configurable because they protect data and order integrity: the market must be open, binary
markets only, $1 minimum order, a fresh account balance, enough cash, and live-money confirmation.

## How the rules are applied

- The governor builds its policy from the workspace's rules on every batch (`trading_rules.to_policy`), so a saved
  change applies to the next batch. Each save increments the version, and every decision records the version it
  used (`policyVersion`, e.g. `user-rules-v3`), so past decisions stay explainable.
- Order checks (`engine.layered_checks`) read the dollar limits, the daily spending ceiling and price freshness
  from the rules. Without rules, a "Trading rules set" check blocks every order.
- Without rules, the governed API refuses proposals, a queued proposal is rejected with "Trading rules are not
  set", the chat agent cannot propose, mandates cannot be saved and agents cannot be resumed.
- `GovernorService(policy=...)` still accepts a fixed policy for replays and tests.

## Existing workspaces

A workspace created before user-set rules has no rules after the upgrade, so its owner goes through the same setup
screen. The old fixed values (the daily ceiling and the dollar limits, plus the previous governor defaults) are
shown under each field as "Your previous value"; they are never applied automatically. In legacy Postgres mode
the rules are stored in `workspace_settings.extra`; the `daily_cap`, `daily_loss_cap`, `market_cap`,
`exposure_cap` and `slippage_max` columns are no longer read or written.

The presenter demo (**▶ DEMO**) is a scripted simulation and keeps its own fixed limits and agent presets.

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
