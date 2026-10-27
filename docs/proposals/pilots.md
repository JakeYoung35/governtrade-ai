# Proposal: pilots (Autopilot-style agents)

Status: proposed, for review. Nothing here is implemented yet; [agent-config.md](../agent-config.md) describes the current contract.

## Summary

Replace today's chat-only agents with **pilots**, modelled on Autopilot's copy trading: you pick a pilot, give it money, and it trades on its own. There is no marketplace yet, so GovernTrade ships **free pilots** that run on the server's Gemini key (the existing free agent, generalized), and users can still build their own pilot with their own model key.

Every pilot trade still goes through the portfolio governor, the owner's trading rules and an approval threshold. That is the main way GovernTrade differs from Autopilot, which simply executes.

## Why

- An agent only trades when the user types a chat message containing a dollar amount (`agent_chat.stated_amounts`). Nothing happens while the user is away.
- Each agent needs four mandate settings (`autoLimitPercent`, `dailyCapPercent`, `minimumPercent`, `newMarketApproval`), all shares of the account's value. They are hard to reason about and partly duplicate the account-wide trading rules.
- The "Markets it focuses on" picker only chooses the creature's outfit; it does not limit what the agent trades, and the agent has no strategy at all beyond the system prompt.

## Decisions

- **Private copies.** Each follower runs their own copy of a pilot in their own workspace. There is no shared pilot brain and no cross-tenant service, so workspace isolation (`tenancy.py`) is unchanged. Followers of the same pilot can see different results.
- **Custom pilots stay.** "Build your own pilot" uses the same setup as a free pilot, plus strategy, categories and a model key.
- **Pilots can sell.** Exits are mirrored as well as entries, instead of holding everything to settlement.
- **Beyond Autopilot**, in the first version:
  - *Governed copying*: the governor, trading rules and an "ask me above $X" threshold apply to every trade.
  - *Explained, scored trades*: each trade shows the pilot's reasoning and probability, and the track record shows calibration (Brier score, `governor/reliability.py`) as well as P&L.
  - *Demo first, no minimum*: follow a pilot on Kalshi Demo, or allocate as little as $10.
  - *Ask your pilot*: chat with a pilot to ask why it traded, or steer it ("skip NFL this week"). Steering carries into scheduled runs; the model still cannot change its own configuration.

### Defaults to confirm in review

- Cadence: hourly, every 4 hours (default), daily or manual (chat only). Free pilots run daily at most, because each run uses one of the workspace's free daily messages.
- Categories are Kalshi's own series categories and are enforced in code, the way stated dollar amounts are today.
- Free pilot templates live in a static catalog, `static/strategies.json`, like `static/providers.json`.
- An approval created by a scheduled run stays open until that pilot's next run, at most 24 hours. Chat approvals keep the `approvalWindowSeconds` rule.
- The allocation is a sub-portfolio that compounds: winnings grow what the pilot can use, losses shrink it.

## Configuration model

These fields replace `personality` and `mandate` on the agent. Unknown agent keys already round-trip through `agents.extra` (`store.py`), so no SQL migration is needed.

```
pilot:              {templateId | null, strategy (text, at most 4,000 characters), categories (at least one), cadence: hourly | 4h | daily | manual}
allocation:         {contributedUsd}   # deposits minus withdrawals, at least $10
approvalAboveUsd:   number             # larger trades wait for the user; 0 means every trade waits
excludedCategories: []                 # the follower's own opt-outs from a template's categories
nextRunAt, lastRunAt
```

- **Equity** = contributed + realized P&L + unrealized P&L.
- **Money at risk** = cost of open positions plus pending and in-flight orders. It can never exceed equity.
- The owner's trading rules (`trading_rules.py`) stay as the hard ceilings: daily spending, kill switch, per-market and open exposure limits. The per-agent minimum reserve and daily cap move into the account rules where they are not already covered. `newMarketApproval` is dropped, because categories now limit which markets a pilot can touch.

## Implementation phases

Each phase is meant to be its own PR.

1. **Catalog and actions**
   - `static/strategies.json` with six free pilots: Macro Desk (economics), Game Day (sports), Ballot Box (politics), Crypto Pulse (crypto), Culture Watch (culture) and Generalist (all). None is named after a real person.
   - A `pilots.py` loader and validator next to `providers.py`.
   - New `workspace.py` actions replace `mandate-save`: `pilot-follow` (template, account, allocation, threshold; reuses the free-provider branch of `agent-save`), `pilot-save` (custom pilot; reuses `validate_model_key`) and `allocation-change` (a withdrawal cannot take equity below money at risk).
   - Existing agents migrate to paused custom pilots with cadence `manual`, allocation = `dailyCapPercent` of current account value and threshold = `autoLimitPercent` of it. The old free agent maps to Generalist. The user reviews the values before resuming.
2. **Ledger, allocation cap and P&L**
   - A per-agent position ledger built from fills (contracts and cost per agent, market and side), reduced by sells and settlements. It replaces the "newest filled order wins" attribution in `GovernorService._exposures`, which is wrong when two agents hold the same market.
   - An `AGENT_ALLOCATION` headroom key in `governor/allocator.py` (`_Usage.headroom`), passed in through `Portfolio` from `_run_account` and stored in batch records so batches still replay exactly.
   - `engine.layered_checks` replaces the agent daily-cap and minimum-reserve rows with the approval threshold.
   - Per-agent performance in the public agent state: realized and unrealized P&L, win rate, money at risk against equity, and reliability.
3. **Scheduler**
   - `nextRunAt` per agent, and the earliest one in the tenant governor marker, so `tenant_has_governor_work` wakes tenants that are due.
   - A separate scheduler thread and small worker pool, so 120-second model turns never block `governor_loop`. Each run follows the same lock / unlock / lock pattern as `Handler.agent_chat`, inside `tenancy.activate`.
   - `AgentChat.begin_scheduled` builds the same job shape as `begin`: the run instruction goes in the system prompt, the last ~10 chat messages carry steering, and the budget is allocation headroom. Proposal IDs are `run-<runId>-<n>`, reserved like `chat-`. The run's summary ("Checked 14 markets, proposed 1") is posted to the chat thread.
4. **Strategy and categories**
   - The system prompt includes the strategy, categories, equity, open positions and threshold.
   - `find_series`, `list_markets`, `propose_trade` and `propose_exit` reject series outside the allowed categories, returning the error to the model.
   - Chat no longer requires a typed dollar amount; allocation headroom is the bound.
5. **Selling**
   - A `propose_exit` tool and a schema version 2 `exit` submission in `governor/api.py` (`marketId`, `side`, `contracts` or `fraction`, optional `minExitPrice`, `reasoning`). Version 1 stays for buys.
   - Exits are reduce-only and limited to the agent's own ledger position. They skip Kelly sizing, the allocation cap and approval, since they reduce risk. The kill switch and execution hard blocks still apply.
   - A Kalshi sell path in `KalshiAdapter.place_order`: immediate-or-cancel at the governor's minimum price. Check Kalshi's reduce-only support before relying on it. `submission unknown` keeps the same reconciliation rules as buys.
   - The replay paper venue learns to sell.
6. **Approvals**: scheduled-run approvals get `approveBy = min(nextRunAt, now + 24h)`. Expiry stays in `Workspace.expire_approvals`, and approving still rechecks price and rules.
7. **Frontend**
   - A Pilots catalog replaces `ui/FreeAgents.js` and the `NewAgent.js` wizard. Follow flow: account (Demo offered first), allocation, threshold, Follow. A "Build your own pilot" card opens the custom form.
   - `AgentDetail.js` shows equity, P&L, money at risk, reliability and next run; trades with reasoning, probability and outcome; add and withdraw; chat; exclusions; pause.
   - `Agents.js` and `AgentLineup.js` show equity and P&L instead of mandate percentages.
8. **Docs and tests**
   - Public agent contract version 3 in [agent-config.md](../agent-config.md), and [governor.md](../governor.md) for the allocation cap and exits.
   - Python and JavaScript tests for each phase, and a replay demo run that enters and then exits a position.

## Verification for the implementation PRs

- `python3 -m unittest discover -s . -p 'test_*.py' -v`
- `for f in tests/javascript/test_*.mjs; do node --experimental-loader ./tests/javascript/module-loader.mjs "$f"; done`
- `python scripts/replay_demo.py`
- On Kalshi Demo: follow Macro Desk with $25, force a run, then check the run summary, the approval above the threshold, the fill, P&L, an exit, and that pausing stops runs. Check that an existing agent migrates to a paused custom pilot with prefilled values.

## Out of scope

- A public marketplace and shared track records across users (needs cross-tenant aggregation).
- Event-driven runs (for example, when a new market opens in a category).
- Retiring the legacy `/api/agent/proposals` route.
