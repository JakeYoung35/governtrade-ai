# Phase 5 implementation and verification

Activity, Rules and Accounts. Verified in the frontend-redesign worktree on 2026-10-14 on the isolated fixture.

## What changed

The first pass existed but was compressed and missed detail: search matched field names (it stringified whole orders),
the chart counted verdicts instead of outcomes, statuses and event kinds showed raw (`awaiting approval`,
`Require_approval`), the ceiling form had no clear or validation, and Accounts had no environment choice cards, no
real-money confirmation styling and no position sides. Rebuilt against the legacy Governance, Monitoring, Trading Rules
and Connections pages:

- **Activity** (`screens/Activity.js`, `activity.js`): counts as before (requested, ran automatically, waiting for you,
  blocked or denied); a daily outcomes chart for the last seven days with proposals, grouped by the viewer's local day
  (filled, needed your approval, blocked or denied, as in the legacy chart), with a legend, hatching for "blocked",
  hover labels and a table view (red and amber fail the colour-blind check, ΔE 2.5 deutan, so colour is never the only
  cue); search over market, order ID, agent name, reasoning and side, word by word; status, agent, category and verdict
  filters with a match count and Clear; orders 50 at a time; events 20 at a time with readable kinds and order links;
  Pause all agents, or a link to Rules when trading is already paused.
- **Rules** (`screens/Rules.js`): the all-agent switch with what on and off mean; the versioned rules form; a lower daily
  ceiling per account with what is in effect, validation and "Use workspace" to clear it; each agent's mandate summary
  with Edit or Set mandate in a dialog.
- **Accounts** (`screens/Accounts.js`): each account with Demo/Live and connection state, key ID mask, value, cash,
  reserved, positions and P/L, when it was read, open positions (YES/NO from the signed contract count, exposure), and
  Test, Settings and keys, Disconnect or Remove. Connect and edit in one dialog: name, Demo or Live as choice cards
  (locked when editing), subaccount 0-63 (locked when editing), where to create the key, key ID and PEM (both or
  neither when rotating), and an explicit real-money confirmation for Live. Keys are cleared from the page after saving.
- `DecisionsTable` takes an `orders` list and an empty message, and shows status pills. A waiting order's status reads
  "Waiting for you" so it no longer repeats the verdict's "Needs approval".
- CSS: a page header inside a section stack no longer doubles the gap below it (Activity, Accounts, Agents).

Fixture: account actions are allowed, with key checks stubbed (nothing is signed or sent) and two seeded open positions.

## Automated checks

- New `tests/javascript/test_activity.mjs`: filters, word search (not field names), counts, daily outcomes, local day.
- All Node suites and 408 Python tests pass (19 skipped: Postgres).

## Browser verification (fixture, port 3130)

At 1280 and 375px (768 and 1600 follow the same grid rules checked in Phase 4):

- Activity: counts, chart with legend and table view, search "deny" gives 1 of 4 with Clear, status pills, events.
- Rules: switch, rules form, account ceiling set to 10% (in effect 10% · $100.00, toast) and back to the workspace
  20%, mandate rows with Edit.
- Accounts: Refresh balances shows two open positions (YES 10, NO 4). Connect a Live account (fixture values only): the
  confirmation appears for Live, it saves as "Not tested", Test connects it and the top bar switches to LIVE MONEY;
  Disconnect deletes the keys and offers Remove; Remove takes it out of the list.

## Not verified

- A real Kalshi Demo key save and test (needs the user's keys; I do not handle them).
- Phase 5 parity boxes stay unticked until that authenticated run.
