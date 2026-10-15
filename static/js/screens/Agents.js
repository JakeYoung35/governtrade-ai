// The agents list: each agent's creature (in its current mood), status, model and account, and the free agent
// listing. Each agent opens its own page (AgentDetail.js) for chat, pause/resume, edit and delete.
import {useState} from 'preact/hooks';
import {html} from '../html.js';
import {Icon} from '../ui/icons.js';
import {Creature} from '../creature/Creature.js';
import {FOCI, colorOf, freshSeed, lookFor} from '../creature/generate.js';
import {moodFor} from '../creature/moods.js';
import {FreeAgents} from '../ui/FreeAgents.js';
import {action} from '../api.js';

export const activeAgents = workspace => (workspace.agentConfigs || []).filter(a => a.status !== 'deleted');
export const connectedAccounts = workspace => (workspace.accounts || []).filter(a => a.status === 'connected');

export function providerName(workspace, agent) {
  const p = workspace.providers?.[agent.provider];
  return p?.shortLabel || p?.label || agent.provider;
}

// "Trading · Kalshi Demo"; an account still named after its connection reads once, not "Kalshi Demo · Kalshi Demo".
export function accountLabel(account) {
  if (!account) return 'No account';
  const venue = `Kalshi ${account.environment === 'live' ? 'Live' : 'Demo'}`;
  return account.name?.trim() && account.name.trim() !== venue ? `${account.name} · ${venue}` : venue;
}

// Colours other agents already wear, so a new or rerolled creature can pick a different one.
export const takenColors = (workspace, exceptId) =>
  activeAgents(workspace).filter(a => a.id !== exceptId).map(a => colorOf(lookFor(a)).hex);

const STATUS = {
  active: ['Active', 'pill-allow'],
  paused: ['Paused', 'pill-neutral'],
  draft: ['Needs a mandate', 'pill-review'],
};

function AgentCard({agent, workspace}) {
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const look = lookFor(agent);
  const account = (workspace.accounts || []).find(a => a.id === agent.accountId);
  const [statusLabel, statusClass] = STATUS[agent.status] || [agent.status, 'pill-neutral'];
  const focus = FOCI.find(f => f.id === look.focus)?.label;

  async function reroll() {
    setError('');
    setSaving(true);
    try {
      await action('agent-look', {id: agent.id, look: {seed: freshSeed(takenColors(workspace, agent.id)), focus: look.focus}});
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  return html`<article class="card agent-card">
    <div class="agent-card-art">
      <${Creature} look=${look} mood=${moodFor(agent, workspace)} size=${76} poke live=${account?.environment === 'live'} />
    </div>
    <div class="agent-card-body">
      <div class="agent-card-title"><h2>${agent.name}</h2><span class=${'pill ' + statusClass}>${statusLabel}</span></div>
      <p>${focus} · ${providerName(workspace, agent)}</p>
      <p class="muted">${accountLabel(account)}</p>
    </div>
    <div class="agent-card-actions">
      ${agent.mandate
        ? html`<a class="btn btn-sm" href=${`/agents/${agent.id}`}>Open</a>`
        : html`<a class="btn btn-primary btn-sm" href=${`/agents/new?agent=${agent.id}`}>Set mandate</a>`}
      <button class="btn btn-ghost btn-sm" onClick=${reroll} disabled=${saving}>${saving ? 'Rerolling' : 'Reroll look'}</button>
    </div>
    ${error ? html`<div class="notice notice-error agent-card-error" role="alert"><${Icon} name="alert" />${error}</div>` : null}
  </article>`;
}

export function Agents({workspace}) {
  const agents = activeAgents(workspace);
  if (!agents.length) {
    return html`<div class="section-stack"><div class="card"><div class="empty">
      <${Creature} look=${{seed: 'gtwelcome', focus: 'general'}} size=${88} poke track />
      <h3>No agents yet</h3>
      <p>An agent finds Kalshi markets and proposes trades. The governor sizes every one, and your rules decide what runs.</p>
      <a class="btn btn-primary" href="/agents/new">New agent</a>
    </div></div><${FreeAgents} workspace=${workspace} /></div>`;
  }
  return html`<div class="section-stack">
    <div class="page-head">
      <p>Each creature shows what its agent is doing right now.</p>
      <a class="btn btn-primary" href="/agents/new">New agent</a>
    </div>
    <div class="agent-grid">${agents.map(a => html`<${AgentCard} key=${a.id} agent=${a} workspace=${workspace} />`)}</div>
    <${FreeAgents} workspace=${workspace} />
  </div>`;
}
