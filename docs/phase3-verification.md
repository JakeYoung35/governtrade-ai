# Phase 3 implementation and verification

Implemented in the frontend-redesign worktree on 2026-10-14. No commits made. Existing Phase 1 and Phase 2 changes preserved.

## Delivered

- Home money totals with per-account Demo/Live labels, daily spend/cap, exposure/cap, loss headroom, stale-balance notice and rules version. Unknown values remain dashes; totals are not silently partial.
- Home approvals and /approvals with sizing rail, cap headroom, approve/deny, local expiry, inline errors and result toasts.
- Agent lineup with persistent moods and transient proposed/trimmed/filled events. Filled animation suppressed for Live accounts.
- Recent decisions with agent creatures and order links.
- Native modal order drawer with evaluation layers/checks, focus containment, Escape, Back, direct links and missing-order state.
- Visible-tab polling at 3 seconds in flight and 15 seconds idle, with auth-generation, busy and newer-action guards.
- Read-only account summaries and allowlisted order governor views. Public serialization never latches a breaker or changes stored state.

## Automated checks

124 Python tests passed across test_home_summary, test_agent_chat, test_governor_service, test_agent_look and test_app_routes.

Node suites passed: test_order_state.mjs, test_polling.mjs (using module-loader.mjs), test_creature.mjs, test_chat_ui.js and test_free_agent_listing.js. The last two protect the retained legacy UI; the new polling suite checks the rebuilt API/store.

`git diff --check` passed.

## Browser verification

Used the Codex browser pane and `python3 -m tests.phase3_preview`, an isolated in-memory fixture using the real governor and Workspace decision action with a fake venue. No credentials, external model calls or trading occurred.

Verified approve to filled, deny to denied, countdown passing to expired with Approve disabled, resized rail, blocked checks, table/card drawer entry, Back, Escape, focus containment and /approvals?order=expire-example direct entry/close.

Reviewed 375, 768, 1280 and 1600px widths. No document-level horizontal overflow; the decisions table scrolls inside its container on small screens. Fixed cap names to use sentence case. Browser fixture values are simulated.

## Persistence trace

No store.py changes or migrations. Account summaries, workspace totals and order governor views are added only to Workspace.public's deep copy. Raw account/order records and the existing extra columns are unchanged. GovernorService.order_governors indexes existing request/decision records once and tolerates slim historic requests without budget fields. Historical details outside the retained governor window may be unavailable and render as dashes.

The summary uses the governor's existing _in_flight and _exposures accounting. It inherits that helper's documented gap for old committed submitted orders without reported fills, rather than changing trading logic in this UI phase.

## Remaining manual acceptance

The running user preview at localhost:3120 was at its local-entry screen. Per the handoff, the agent did not enter or sign in for the user. An authenticated Kalshi Demo chat-to-proposal run remains unverified. The existing server also needs a restart to pick up Python changes before that acceptance run. PostgreSQL round-trip execution remains unverified because no local Postgres is available.

Phase 4 agent details/chat and Phase 5 activity/accounts remain planned placeholders, as in the handoff.
