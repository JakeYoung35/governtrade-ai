// The Activity screen's filter choices. Filtering, search, the headline counts and the daily outcomes chart run on the
// server (order_views.py), which has every order; the browser state carries only the newest.

// Order statuses offered in the status filter, in the order a trade moves through them.
// Only statuses the server assigns (see the test in tests/javascript/test_activity.mjs); an expired approval is 'cancelled'.
export const STATUS_FILTERS = ['awaiting approval', 'submitted', 'submission unknown', 'open',
  'filled', 'settled', 'blocked', 'denied', 'rejected', 'cancelled'];
export const CATEGORIES = ['crypto', 'sports', 'economics', 'politics', 'culture', 'other'];
export const DECISIONS = [['ALLOW', 'Allowed'], ['REQUIRE_APPROVAL', 'Needs approval'], ['BLOCK', 'Blocked']];

// The viewer's IANA time zone, so the daily chart groups orders by the viewer's own calendar days.
export function viewerTimeZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}
