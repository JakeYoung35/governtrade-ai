// Pure helpers shared by polling, cards and tests. The workspace state carries a bounded window of orders
// (order_views.py): recentOrders (the newest), pendingOrders (the first waiting for approval) and orderStats (counts
// over every order).
export const pendingOrders = workspace => workspace.pendingOrders || [];
// Every order the state carries, once each.
export function windowOrders(workspace) {
  const orders = new Map();
  for (const o of [...(workspace?.recentOrders || []), ...(workspace?.pendingOrders || [])]) orders.set(o.id, o);
  return [...orders.values()];
}
export function countdown(deadline, now = Date.now()) {
  if (!deadline) return {expired: false, label: 'No expiry recorded'};
  const end = Date.parse(deadline);
  if (!Number.isFinite(end)) return {expired: true, label: 'Expiry unavailable'};
  const seconds = Math.max(0, Math.ceil((end - now) / 1000));
  return {expired: seconds === 0, label: seconds === 0 ? 'Expired' : `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')} remaining`};
}
// The server counts orders still in flight over every order (order_views.IN_FLIGHT): waiting for approval, resting,
// submitted, partially filled, open, or with an unknown submission.
export function hasInFlight(workspace) {
  return (workspace?.orderStats?.inFlight || 0) > 0 ||
    Object.values(workspace?.chatProposals || {}).flat().some(p => ['queued', 'approved'].includes(p.status));
}
export function moodEvents(previous, next) {
  if (!previous) return {};
  // Moods come from the orders the state carries: the newest and the waiting ones, which is where they happen.
  const prior = new Map(windowOrders(previous).map(o => [o.id, o]));
  const accounts = new Map((next.accounts || []).map(a => [a.id, a]));
  const events = {};
  // An order missing from the previous window is new only if it is newer than everything that window could have left
  // out; an older one entering it (a waiting order past the first page, say) is not news.
  const recent = previous.recentOrders || [];
  const known = recent.length ? Date.parse(recent.at(-1).time) : -Infinity;
  const byTime = (a, b) => (Date.parse(a.time) || 0) - (Date.parse(b.time) || 0);
  for (const order of windowOrders(next).sort(byTime)) {  // oldest first, so an agent's newest event wins
    const old = prior.get(order.id), g = order.governor;
    if (!old && !(Date.parse(order.time) > known)) continue;
    const trimmed = g && (g.decision === 'resized' || (g.approvedBudgetUsd != null && g.maxBudgetUsd != null && Number(g.approvedBudgetUsd) < Number(g.maxBudgetUsd)));
    const wasTrimmed = old?.governor && (old.governor.decision === 'resized' || (old.governor.approvedBudgetUsd != null && old.governor.maxBudgetUsd != null && Number(old.governor.approvedBudgetUsd) < Number(old.governor.maxBudgetUsd)));
    let mood = !old ? 'proposed' : null;
    if (trimmed && !wasTrimmed) mood = 'trimmed';
    if (old && old.status !== 'filled' && order.status === 'filled' && accounts.get(order.accountId)?.environment !== 'live') mood = 'filled';
    if (mood) events[order.agent] = mood;
  }
  return events;
}
