// An agent's persistent mood, from what is actually happening to it. One-shot moods (proposed, trimmed, filled)
// are events, not states; the screens that see those events pass them to <Creature> directly.

const BLOCK_MOOD_MS = 10 * 60 * 1000;

export function moodFor(agent, workspace, {now = Date.now(), thinking = false} = {}) {
  const account = (workspace.accounts || []).find(a => a.id === agent.accountId);
  if (account && (account.error || account.status === 'error')) return 'offline';
  if (agent.status === 'paused' || workspace.permissions?.automation === false) return 'paused';
  if (thinking) return 'thinking';
  const orders = (workspace.orders || []).filter(o => o.agent === agent.id);
  if (orders.some(o => o.status === 'awaiting approval')) return 'waiting';
  const last = orders.reduce((latest, o) => (!latest || new Date(o.time) > new Date(latest.time) ? o : latest), null);
  if (last && last.decision === 'BLOCK' && now - new Date(last.time) < BLOCK_MOOD_MS) return 'blocked';
  return 'idle';
}
