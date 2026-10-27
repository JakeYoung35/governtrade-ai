// Pure helpers for an agent conversation, shared by the agent page and the Node tests.

// Each chat proposal records the index of the customer message that asked for it. It is shown under the agent's
// reply to that message. If the reply was never stored (the model turn failed or the connection dropped), it is
// shown under the customer message instead, so a proposal is never hidden.
export function thread(messages = [], proposals = []) {
  const items = messages.map((message, index) => ({message, index, proposals: []}));
  for (const proposal of proposals) {
    const asked = items[proposal.messageIndex];
    if (!asked) continue;
    const after = items.slice(proposal.messageIndex + 1);
    const next = after.findIndex(item => item.message.role === 'customer');
    const reply = (next === -1 ? after : after.slice(0, next)).find(item => item.message.role === 'agent');
    (reply || asked).proposals.push(proposal);
  }
  return items;
}

// Why the agent can talk but not propose trades right now, or null. Mirrors AgentChat._trading_blocked on the server.
export function tradingBlocked(workspace, agent, account) {
  if (!workspace.rules?.values) return 'Set your trading rules first.';
  if (!workspace.permissions?.automation) return 'All-agent trading is switched off in Rules.';
  if (!agent.mandate) return `Set ${agent.name}'s mandate first.`;
  if (agent.status !== 'active') return `${agent.name} is paused. Resume it to trade.`;
  if (account?.status !== 'connected') return 'Its Kalshi account is not connected. Test it under Accounts.';
  return null;
}

// After a failed send, the message may still have reached the agent. It did only if the conversation grew past the
// length it had when the message was sent and a newer customer message carries the same text (an identical earlier
// message does not count). Restore the draft only if it did not.
export function wasDelivered(messages = [], lengthBefore, text) {
  return messages.slice(lengthBefore).some(m => m.role === 'customer' && m.text.trim() === text.trim());
}

// The governor's outcome for one proposal, in words. Returns null while it has not decided.
export function governorLine(proposal, {money, reasonText}) {
  const d = proposal.decision;
  if (!d) return null;
  const reasons = d.reasonCodes?.length ? ` · ${d.reasonCodes.map(reasonText).join(', ')}` : '';
  if (d.decision === 'rejected') return `Governor rejected it${reasons}`;
  const contracts = `${d.contracts} contract${Number(d.contracts) === 1 ? '' : 's'}`;
  return `Governor ${d.decision === 'resized' ? 'resized it to' : 'approved'} ${money(d.approvedBudgetUsd)} · ${contracts} at up to ${money(d.limitPrice)}${reasons}`;
}
