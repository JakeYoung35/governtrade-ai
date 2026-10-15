// The free agent listing on the Agents page: GovernTrade's shared Gemini model, offered when the server has a free
// key. One per workspace. Adding it saves the agent and goes straight to its mandate, the only thing to set up.
import {useState} from 'preact/hooks';
import {html} from '../html.js';
import {Icon} from './icons.js';
import {action} from '../api.js';
import {navigate} from '../router.js';
import {freshSeed} from '../creature/generate.js';
import {accountLabel, connectedAccounts, takenColors} from '../screens/Agents.js';
import {freeMessagesLeft} from '../screens/AgentDetail.js';

export function FreeAgents({workspace}) {
  const accounts = connectedAccounts(workspace);
  const [accountId, setAccountId] = useState(accounts[0]?.id || '');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const entry = Object.entries(workspace.providers || {}).find(([, p]) => p.free);
  if (!entry) return null;
  const [provider, p] = entry;
  const mine = (workspace.agentConfigs || []).find(a => a.provider === provider && a.status !== 'deleted');
  const quota = freeMessagesLeft(workspace, {provider});
  const chosen = accounts.some(a => a.id === accountId) ? accountId : accounts[0]?.id;

  async function add() {
    setError('');
    setSaving(true);
    try {
      const result = await action('agent-save', {name: 'Free agent', provider, model: p.models[0], accountId: chosen,
        look: {seed: freshSeed(takenColors(workspace)), focus: 'general'}});
      if (result?.agentId) navigate(`/agents/new?agent=${result.agentId}`);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return html`<section class="card free-agent">
    <div class="card-head"><h2>${p.shortLabel || p.label}</h2><span class="pill pill-allow">Free</span></div>
    <div class="card-body free-agent-body">
      <p>Ready to use: ${p.models[0]} on Gemini, set up for you. No API key needed and ${p.dailyMessages} free messages a day for your workspace. You only set its mandate.</p>
      <p class="fine-print">${p.notice}</p>
      ${mine ? html`<div class="free-agent-actions">
          <span class="muted">${quota.left} of ${quota.total} messages left today</span>
          <a class="btn" href=${mine.mandate ? `/agents/${mine.id}` : `/agents/new?agent=${mine.id}`}>${mine.mandate ? `Open ${mine.name}` : 'Set its mandate'}</a>
        </div>`
      : !accounts.length ? html`<div class="notice notice-warn"><${Icon} name="info" /><span>Connect and test a Kalshi account first. <a href="/accounts">Connect Kalshi</a></span></div>`
      : html`<div class="free-agent-actions">
          ${accounts.length > 1 ? html`<label class="field"><span class="field-label">Kalshi account</span>
            <select class="input select" value=${chosen} onChange=${e => setAccountId(e.currentTarget.value)}>
              ${accounts.map(a => html`<option value=${a.id}>${accountLabel(a)} · subaccount ${a.subaccount || 0}</option>`)}
            </select></label>` : html`<span class="muted">Trades on ${accountLabel(accounts[0])}</span>`}
          <button class="btn btn-primary" disabled=${saving} onClick=${add}>${saving ? html`<span class="spinner" aria-hidden="true"></span>Adding` : 'Use the free agent'}</button>
        </div>`}
      ${error ? html`<div class="notice notice-error" role="alert"><${Icon} name="alert" /><span>${error}</span></div>` : null}
    </div>
  </section>`;
}
