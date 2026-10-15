# Phase 4 implementation and verification

Agents: the list, the agent page with chat, the mandate editor, look, pause/resume/edit/delete, and the free agent.
Verified in the frontend-redesign worktree on 2026-10-14. No commits made.

## What changed

The first Phase 4 pass existed but was rough: proposals sat under the user's message, governor reason codes showed raw
(`positionAlreadyOpen`), statuses were lowercase, the free-message quota showed on every agent, chat bubbles were
indistinguishable, and the free agent listing and agent page were written in a compressed style unlike the rest of the
code. Rebuilt:

- `screens/AgentDetail.js`: hero (creature in its live mood, status pill, focus, provider, model, account, key mask or
  free quota) with Pause/Resume, Set mandate (drafts), Edit agent, Change look and Delete. Conversation with
  your/agent bubbles, the agent's creature as avatar, a thinking state, an empty state with an example, Enter to send on
  a keyboard, the draft kept on failure unless the message was delivered, and a notice when the agent can talk but not
  trade (the server's reasons, in order). Proposal cards under the agent's reply: market, status pill, side, ask, agent
  estimate, the governor's outcome in words, the blocking check when the order was blocked, and a link to the order
  drawer. Proposals awaiting approval render as the shared ApprovalCard. Mandate card with each limit in % and dollars
  and today's spend against the daily cap. Orders awaiting approval from this agent outside the chat show as one link to
  Approvals. Dialogs for edit (keeps a retired model selectable, asks for a key only when needed), look (draft, then
  save) and the mandate (MandateStep with Cancel). Toasts confirm saves and deletion.
- `chat.js` (new, pure): `thread`, `tradingBlocked`, `wasDelivered`, `governorLine`.
- `governor-text.js` (new): reason codes and order statuses in words, shared with GovernorRail.
- `creature/useAgentMood.js` (new): one-shot mood events, shared by AgentLineup and the agent page.
- `ui/FreeAgents.js`: rewritten listing with the account choice, the quota, and Open/Set mandate once added.
- `screens/Agents.js`: an account named "Kalshi Demo" no longer reads "Kalshi Demo · Kalshi Demo".
- `main.js`: the top bar and tab title show the agent's name on its page.
- CSS: agent page, chat, dialog actions and sticky dialog header, danger buttons, notice links, free agent card. The toast
  sits 24px from the bottom on desktop and above the tab bar on mobile (it floated as if a tab bar were there).

Fixture (`tests/phase3_preview.py`): responses now include `result` like server.py (without it, saving a mandate,
creating an agent or adding the free agent never moved on); `Cache-Control: no-cache` like server.py; the scripted
model proposes the dollar amount typed (the server refuses any other amount), with `[slow]` and `[fail]` switches; the
free agent is offered with no Gemini call; agents carry a key mask.

## Automated checks

- New Node suite `tests/javascript/test_chat_thread.mjs`: proposal threading (reply, no reply, later messages,
  ordering, out-of-range), delivery detection, governor wording, trading-blocked reasons in the server's order, statuses.
- All suites pass: test_chat_thread, test_order_state, test_creature, test_polling (with module-loader), test_chat_ui,
  test_free_agent_listing, and 402 Python tests (19 skipped: Postgres).

## Browser verification (fixture, port 3130)

Reproduced as a user would, at 1280, 768, 375 and 1600px:

- Agents list, open an agent, top bar shows its name.
- Chat: example button fills the box, Enter sends, thinking state with dots, reply with a proposal card (governor
  approved, then blocked by the market cap with the reason shown), model failure shown as the agent's reply.
- Edit agent: rename saved, title and hero update, toast. Retired model shown as "(no longer offered)".
- Change look: reroll and focus saved.
- Mandate: edit 2.5% to 3%, saved, dialog closes, card shows 3% / $30.00.
- Pause: pill, sleeping creature, Resume as primary, chat notice "is paused. Resume it to trade". Resume works.
- Delete: confirmation, back to Agents with a toast.
- Free agent: offered, added, lands on its mandate, then listed with "20 of 20 messages left today" and Open.
- Mobile: hero actions as a 2x2 grid, chat and mandate stacked, page scrolls over the conversation.

## Not verified

- A real model turn and a real Kalshi Demo proposal (needs the user's local workspace and keys; I do not sign in).
- The connection-lost path of sendChat in a browser (covered by `wasDelivered` tests and the unchanged api.js logic).
- Phase 4 parity items in the plan are ticked only after the authenticated Demo run above.
