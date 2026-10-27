# Phase 7 polish and verification

Mobile, worst-case data, accessibility, motion and a pixel pass over every screen. 2026-10-15, on the fixtures.

## Pixel pass fixes

- Home: the money strip says what its second figures are ("of $200.00 a day", "of $300.00 allowed", "before new trades
  stop"); the account summary reads "1 account: Kalshi Demo" instead of "Kalshi Demo (Demo)"; notices sit inside the
  card padding; the agent lineup packs to the left instead of spreading two creatures across the page; moods read as
  words ("Waiting for you", "Ready") instead of "waiting" and "idle".
- Approval card: "Buy YES · $49.68"; the agent's reasoning is labelled and clamped to three lines with Show all;
  "Details" instead of "View details"; the expired state reads "Expired: too late to approve".
- Order drawer: market, agent, account and time first, then the verdict (and the status only when it says something
  else), an order facts box (order, actual cost, market ticker, order ID), the governor sizing or the approval, the
  agent's reasoning, and the evaluations with layers in words ("Your rules", "Agent mandate", "Execution"), the checks
  that blocked or held the order first and the passed ones folded under "N checks passed". No duplicated order line.
- `/demo` no longer says "arrives in a later phase of the redesign"; it points to Kalshi Demo.

## Mobile

- Dialogs are bottom sheets under 600px, sliding up; the order drawer slides in from the right.
- Number boxes in the rules and mandate forms are 16px on phones, so iOS does not zoom the page on focus.
- The top bars and the drawer header leave room for the notch when the app runs from the home screen.
- Hover colours on the new danger buttons, lineup and notice links do not stick after a tap.

## Worst-case data (`FIXTURE_DATA=worst`, launch config `redesign-fixture-worst`, port 3131)

Limits come from the server: agent and account names 100 characters, amounts $1,000,000, reasoning 8,000, chat 2,000 and
6,000; market names, events and list lengths are unbounded. The dataset mixes a 100-character agent name, "Jo", "X",
an emoji name, Arabic and Vietnamese names, an 85-character account name, nine-digit balances, negative P/L, 1,200
orders (150 waiting), 8,000-character reasoning with long URLs, a 46-character ticker, 300 events, a 2,000-character
chat message, a 6,000-character reply and one free message left.

| # | Severity | What broke | Fix |
| --- | --- | --- | --- |
| 1 | Broken | Home rendered all 150 waiting approvals, each with up to 8,000 characters of reasoning | Home shows 3 and "See all 150"; Approvals pages 20 at a time; reasoning clamped to 3 lines in cards |
| 2 | Ugly | Agent cards: the Open button stretched to the card height; a 100-character name took six lines; the long account label repeated in full | Grid rows `1fr auto`, actions centred; name clamped to two lines and the account to one, both with the full text as a tooltip and in full on the agent page |
| 3 | Fragile | 50 animated creatures in the orders table repainted constantly | Table creatures are still (`creature-still`) |

Held up: long names wrap on the agent page, top bar and drawer; Arabic and emoji names render in tables; $999,999.99,
$89,938,889.00 and -$123,456.78 fit every money cell; long URLs break inside chat bubbles and reasoning; the
6,000-character reply scrolls inside the conversation; "1 day", "1 account", "1 of 20" read correctly.

## Accessibility

- Contrast: every text and background pair in use passes WCAG AA; the lowest is the Allowed pill at 4.56:1.
- Automated pass on Home, Approvals, Agents, an agent page, New agent, Activity, Rules and Accounts: no unnamed buttons
  or links, no unlabelled fields, one h1 per page, a main landmark, no skipped heading levels.
- Added a skip link (the first Tab shows "Skip to content") and focus moves to the page content after moving to another
  page, so keyboard and screen reader users start there. Opening the order drawer does not move it.
- Dialogs and the drawer are native modal dialogs: focus stays inside, Escape closes, focus returns.

## Motion

Everything animates transform and opacity only (creatures, sheets, drawer, dots); eye tracking is batched per frame;
all of it stops under reduced motion.

## Decision for the user (not changed)

`/api/state` sends the whole workspace on every poll. In the worst case that is 7.7 MB, fetched every 3 seconds while
approvals wait (about 40 ms of main-thread work per poll, and roughly 2.5 MB/s on a phone). Paging orders and events on
the server, and leaving long reasoning out of the list payload, would fix it; it is a backend change with its own tests.
