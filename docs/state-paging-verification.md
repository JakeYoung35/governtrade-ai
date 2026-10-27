# State payload paging and verification

`/api/state` sent the whole workspace on every poll: 7.7 MB on the worst-case fixture, every 3 seconds while orders were
in flight. It now carries a bounded window, and the screens read the rest page by page. 2026-10-15, on the fixtures.

## What the state carries (order_views.py)

- `recentOrders`: the newest 20 orders, and `pendingOrders`: the first 50 waiting for approval, both as list rows. A row
  has no evaluations, the reasoning cut to 280 characters (`requestLength` is the full length), a `reviewed` flag, the
  `blockReason` of a blocked order, and the full governor sizing only while the order waits (the card shows the rail).
- `orderStats`: `total`, `awaiting` and `inFlight`, counted over every order (the Approvals badge, Home, polling).
- Each agent's `activity`: how many of its orders wait and its latest order (the creature's mood).
- The newest 20 `events` and `eventCount`. The idempotency `keys` map, which grew with every order, no longer leaves the
  server.

## Paged reads (same origin, no venue refresh, the signed-in user's workspace only)

- `GET /api/orders?status&agent&category&decision&query&offset&limit`: Activity's filters and search (the full reasoning
  is searched, not the preview), and Approvals past the first 50. At most 100 rows per read.
- `GET /api/orders/<id>`: the full order for the drawer and for Show all on a card's reasoning; 404 when it is not in
  the workspace.
- `GET /api/activity?tz=<IANA zone>`: the headline counts and the daily chart, grouped by the viewer's calendar days.
  An unknown, overlong or directory-like zone name falls back to UTC.
- `GET /api/events?offset&limit`: Activity's older events.

Paging values must be ASCII digits in range; anything else is a 400.

## Measured on redesign-fixture-worst (1,200 orders, 8,000-character reasoning, 300 events)

| Read | Before | After |
| --- | --- | --- |
| `/api/state` | 7,761 KB | 112 KB (pendingOrders 73 KB, recentOrders 15 KB) |
| `/api/orders` (first 50) | - | 36 KB |
| `/api/orders/<id>` | - | 12 KB |
| `/api/activity` | - | 1 KB |

`test_worst_case_state_stays_small` keeps the state under 300 KB for 1,200 such orders.

## Checked in the browser

- Activity: counts, chart, filters and search (one read per pause in typing), Show more across two reads (150 unique
  rows), older events, and a drawer for an order the state does not carry (full reasoning, sizing, evaluations).
- Approvals: 150 waiting, 20 at a time; past 50 the page reads from the server (60 unique cards, in order). Show all
  reads the full reasoning. Deny and approve update the count, the nav badge and the toast (from the action's result).
- Agents and an agent page: moods and the "N orders waiting" notice come from the server's per-agent counts.
- A missing order shows "Order unavailable"; a slow read shows "Loading" without moving the chart card (same height
  loading and loaded); returning to a screen shows its last answer at once while it reads again (kept per signed-in
  user, never across a sign-in change).
- 375, 768, 1280 and 1600 px: no horizontal page scroll.

Not exercised in the browser: an approval card inside a chat conversation, and a chat proposal whose order was blocked
(the fixture's governor rejected the chat proposal before an order existed). Both read fields covered by tests
(`proposal_views` carries the order status, decision and `blockReason`).
