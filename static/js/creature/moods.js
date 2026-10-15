// An agent's persistent mood, from what is actually happening to it. One-shot moods (proposed, trimmed, filled)
// are events, not states; the screens that see those events pass them to <Creature> directly.

const BLOCK_MOOD_MS = 10 * 60 * 1000;

// A persistent mood in words, for a caption under a creature.
const MOOD_WORDS = {idle: 'Ready', thinking: 'Thinking', waiting: 'Waiting for you', blocked: 'Recently blocked', paused: 'Paused', offline: 'Account offline'};
export const moodWords = mood => MOOD_WORDS[mood] || mood;

export function moodFor(agent, workspace, {now = Date.now(), thinking = false} = {}) {
  const account = (workspace.accounts || []).find(a => a.id === agent.accountId);
  if (account && (account.error || account.status === 'error')) return 'offline';
  if (agent.status === 'paused' || workspace.permissions?.automation === false) return 'paused';
  if (thinking) return 'thinking';
  // The server summarises each agent's orders (order_views.agent_activity): the state carries only the newest.
  const activity = (workspace.agentConfigs || []).find(a => a.id === agent.id)?.activity || agent.activity || {};
  if (activity.awaiting > 0) return 'waiting';
  const last = activity.lastOrder;
  if (last && last.decision === 'BLOCK' && now - new Date(last.time) < BLOCK_MOOD_MS) return 'blocked';
  return 'idle';
}
