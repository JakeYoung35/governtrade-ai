// The user's trading rules: the all-agent switch, the rules themselves (versioned), an optional lower daily ceiling
// per account, and each agent's mandate. Every trade must pass all of them.
import {useState} from 'preact/hooks';
import {html} from '../html.js';
import {Icon} from '../ui/icons.js';
import {RulesForm} from '../ui/RulesForm.js';
import {Dialog} from '../ui/Dialog.js';
import {action} from '../api.js';
import {useStore, setStore} from '../store.js';
import {money} from '../format.js';
import {Creature} from '../creature/Creature.js';
import {lookFor} from '../creature/generate.js';
import {accountLabel, activeAgents} from './Agents.js';
import {MandateStep} from './NewAgent.js';

function ErrorNote({message}) {
  return message ? html`<div class="notice notice-error" role="alert"><${Icon} name="alert" /><span>${message}</span></div>` : null;
}

function TradingSwitch({workspace}) {
  const {busy} = useStore();
  const [error, setError] = useState('');
  const on = workspace.permissions?.automation !== false;
  async function toggle(e) {
    const automation = e.currentTarget.checked;
    setError('');
    try {
      await action('permissions', {scope: 'defaults', values: {automation}});
      setStore({toast: automation ? 'All-agent trading is on. Resume paused agents from their pages.' : 'All agents paused.'});
    } catch (err) {
      setError(err.message);
    }
  }
  return html`<section class="card card-body section-stack-sm">
    <label class="switch-row">
      <span><strong>All-agent trading</strong><small>${on
        ? 'On. Switching it off pauses every agent at once.'
        : 'Off: every agent is paused. Switching it on does not resume them; resume each one from its page.'}</small></span>
      <input type="checkbox" role="switch" class="switch" checked=${on} disabled=${busy} onChange=${toggle} />
    </label>
    <${ErrorNote} message=${error} />
  </section>`;
}

function Ceiling({account, workspacePercent}) {
  const {busy} = useStore();
  const saved = account.ceiling?.dailyPercent;
  const [value, setValue] = useState(saved ?? '');
  const [error, setError] = useState('');
  const number = Number(value);
  const valid = value === '' || (String(value).trim() !== '' && Number.isFinite(number) && number >= 0 && number <= 100);
  const changed = String(value) !== String(saved ?? '');
  async function save(dailyPercent) {
    setError('');
    try {
      await action('permissions', {scope: account.id, values: {dailyPercent}});
      setStore({toast: dailyPercent == null ? `${account.name} uses your workspace ceiling again.` : `${account.name}'s ceiling saved.`});
    } catch (err) {
      setError(err.message);
    }
  }
  const effective = account.effectiveCeiling;
  return html`<form class="ceiling-row" onSubmit=${e => { e.preventDefault(); if (valid && changed) save(value === '' ? null : number); }}>
    <div class="ceiling-account"><strong>${accountLabel(account)}</strong>
      <small>In effect: ${effective?.dailyPercent ?? workspacePercent}% a day${effective?.dailyCap != null ? ` · ${money(effective.dailyCap)}` : ''}</small></div>
    <div class="ceiling-input">
      <div class="rule-number" aria-invalid=${valid ? null : 'true'}>
        <input type="number" inputmode="decimal" min="0" max="100" step="any" aria-label=${`Lower daily ceiling for ${account.name}, percent`}
          placeholder=${String(workspacePercent ?? '')} value=${value} onInput=${e => setValue(e.currentTarget.value)} />
        <span>%</span>
      </div>
      <button class="btn btn-sm" disabled=${busy || !valid || !changed}>Save</button>
      ${saved != null ? html`<button type="button" class="btn btn-ghost btn-sm" disabled=${busy} onClick=${() => save(null)}>Use workspace</button>` : null}
    </div>
    ${!valid ? html`<p class="rule-error" role="alert">Enter a percentage from 0 to 100, or leave it empty.</p>` : null}
    <${ErrorNote} message=${error} />
  </form>`;
}

function Mandates({workspace}) {
  const [editing, setEditing] = useState(null);
  const agents = activeAgents(workspace);
  const agent = agents.find(a => a.id === editing);
  const summary = m => `Automatic up to ${m.autoLimitPercent}% · ${m.dailyCapPercent}% a day · keeps ${m.minimumPercent}% in cash${m.newMarketApproval ? ' · new markets need approval' : ''}`;
  return html`<section class="card">
    <div class="card-head"><h2>Agent mandates</h2></div>
    ${!agents.length ? html`<div class="empty"><p>Each agent gets its own limits on top of these rules. Create an agent to set its mandate.</p><a class="btn btn-sm" href="/agents/new">New agent</a></div>` : html`
      <ul class="mandate-list">${agents.map(a => html`<li key=${a.id}>
        <${Creature} look=${lookFor(a)} size=${32} />
        <div><a href=${`/agents/${a.id}`}>${a.name}</a><small>${a.mandate ? summary(a.mandate) : 'No mandate yet, so it cannot trade.'}</small></div>
        <button class=${a.mandate ? 'btn btn-sm' : 'btn btn-primary btn-sm'} onClick=${() => setEditing(a.id)}>${a.mandate ? 'Edit' : 'Set mandate'}</button>
      </li>`)}</ul>`}
    ${agent ? html`<${Dialog} title=${`${agent.name}'s mandate`} onClose=${() => setEditing(null)}>
      <${MandateStep} agent=${agent} workspace=${workspace} onCancel=${() => setEditing(null)}
        onSaved=${() => { setEditing(null); setStore({toast: `${agent.name}'s mandate saved. New proposals use it.`}); }} />
    </${Dialog}>` : null}
  </section>`;
}

export function Rules({workspace}) {
  const accounts = (workspace.accounts || []).filter(a => a.status !== 'removed');
  const workspacePercent = workspace.rules?.values?.dailySpendingPercent;
  return html`<div class="section-stack">
    <div class="page-head"><p>Every trade must pass these rules and its agent's mandate. Blocks cannot be approved away.</p></div>
    <${TradingSwitch} workspace=${workspace} />
    <${RulesForm} workspace=${workspace} onSaved=${() => setStore({toast: 'Trading rules saved as a new version.'})} />
    <section class="card">
      <div class="card-head"><h2>Lower ceiling for one account</h2></div>
      <div class="card-body section-stack-sm">
        <p>Optional. An account can spend less per day than your workspace ceiling of ${workspacePercent ?? '–'}%. The lower one applies.</p>
        ${accounts.length ? accounts.map(a => html`<${Ceiling} key=${`${a.id}:${a.ceiling?.dailyPercent ?? ''}`} account=${a} workspacePercent=${workspacePercent} />`)
          : html`<p class="muted">Connect a Kalshi account to set one. <a href="/accounts">Accounts</a></p>`}
      </div>
    </section>
    <${Mandates} workspace=${workspace} />
  </div>`;
}
