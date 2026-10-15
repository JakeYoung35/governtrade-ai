// Pure helpers shared by polling, cards and tests.
export const pendingOrders = workspace => (workspace.orders || []).filter(o => o.status === 'awaiting approval');
export function countdown(deadline, now = Date.now()) {
  if (!deadline) return {expired: false, label: 'No expiry recorded'};
  const end = Date.parse(deadline);
  if (!Number.isFinite(end)) return {expired: true, label: 'Expiry unavailable'};
  const seconds = Math.max(0, Math.ceil((end - now) / 1000));
  return {expired: seconds === 0, label: seconds === 0 ? 'Expired' : `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')} remaining`};
}
const IN_FLIGHT = new Set(['awaiting approval', 'resting', 'submitted', 'partially filled', 'open', 'submission unknown', 'unknown']);
export function hasInFlight(workspace) {
  return (workspace?.orders || []).some(o => IN_FLIGHT.has(o.status)) ||
    Object.values(workspace?.chatProposals || {}).flat().some(p => ['queued', 'approved'].includes(p.status));
}
export function moodEvents(previous, next) {
  if (!previous) return {};
  const prior = new Map((previous.orders || []).map(o => [o.id, o]));
  const accounts = new Map((next.accounts || []).map(a => [a.id, a]));
  const events = {};
  for (const order of [...(next.orders || [])].reverse()) {
    const old = prior.get(order.id), g = order.governor;
    const trimmed = g && (g.decision === 'resized' || (g.approvedBudgetUsd != null && g.maxBudgetUsd != null && Number(g.approvedBudgetUsd) < Number(g.maxBudgetUsd)));
    const wasTrimmed = old?.governor && (old.governor.decision === 'resized' || (old.governor.approvedBudgetUsd != null && old.governor.maxBudgetUsd != null && Number(old.governor.approvedBudgetUsd) < Number(old.governor.maxBudgetUsd)));
    let mood = !old ? 'proposed' : null;
    if (trimmed && !wasTrimmed) mood = 'trimmed';
    if (old && old.status !== 'filled' && order.status === 'filled' && accounts.get(order.accountId)?.environment !== 'live') mood = 'filled';
    if (mood) events[order.agent] = mood;
  }
  return events;
}
