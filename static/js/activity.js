// Pure helpers for the Activity screen: order filters, the headline counts and the daily outcomes chart.

// Order statuses offered in the status filter, in the order a trade moves through them.
export const STATUS_FILTERS = ['awaiting approval', 'submitted', 'submission unknown', 'resting', 'open', 'partially filled',
  'filled', 'settled', 'blocked', 'denied', 'rejected', 'expired', 'cancelled'];
export const CATEGORIES = ['crypto', 'sports', 'economics', 'politics', 'culture', 'other'];
export const DECISIONS = [['ALLOW', 'Allowed'], ['REQUIRE_APPROVAL', 'Needs approval'], ['BLOCK', 'Blocked']];

const blockedOrDenied = o => o.status === 'blocked' || o.status === 'denied';
const reviewed = o => (o.evaluations || []).some(e => e.outcome === 'REQUIRE_APPROVAL');

// Search looks at what a person would type: the market, the order ID, the agent's name, its reasoning, the side and the
// status, never field names.
export function filterOrders(orders = [], filters = {}, agents = new Map()) {
  const words = (filters.query || '').toLowerCase().trim().split(/\s+/).filter(Boolean);
  return orders.filter(o => {
    if (filters.status && o.status !== filters.status) return false;
    if (filters.agent && o.agent !== filters.agent) return false;
    if (filters.category && o.market?.agent !== filters.category) return false;  // market.agent is the market's category
    if (filters.decision && o.decision !== filters.decision) return false;
    if (!words.length) return true;
    const text = [o.market?.name, o.market?.id, o.id, agents.get(o.agent)?.name, o.request, o.direction, o.status].filter(Boolean).join(' ').toLowerCase();
    return words.every(w => text.includes(w));
  });
}

export function activityCounts(orders = []) {
  return {
    requested: orders.length,
    automatic: orders.filter(o => o.automatic && o.filled).length,
    awaiting: orders.filter(o => o.status === 'awaiting approval').length,
    blocked: orders.filter(blockedOrDenied).length,
  };
}

// The viewer's own calendar day of a timestamp, 'YYYY-MM-DD', or null when it is not a time.
export function localDay(time) {
  const d = new Date(time);
  if (!time || isNaN(d)) return null;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// The last `days` days that had proposals, oldest first: [{day: 'YYYY-MM-DD', filled, review, blocked}], in the
// viewer's time zone. An order can count as both review and filled (approved, then filled).
export function dailyOutcomes(orders = [], days = 7, dayOf = localDay) {
  const byDay = new Map();
  for (const o of orders) {
    const day = dayOf(o.time);
    if (!day) continue;
    const counts = byDay.get(day) || {day, filled: 0, review: 0, blocked: 0};
    if (o.filled) counts.filled++;
    if (reviewed(o)) counts.review++;
    if (blockedOrDenied(o)) counts.blocked++;
    byDay.set(day, counts);
  }
  return [...byDay.values()].sort((a, b) => a.day.localeCompare(b.day)).slice(-days);
}
