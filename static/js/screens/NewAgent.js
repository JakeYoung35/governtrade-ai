// Creating an agent in three steps: its creature and name, its model and Kalshi account, then its mandate.
// The agent is saved at the end of step 2 (the server validates the model key there); step 3 sets the mandate.
// /agents/new?agent=<id> resumes at the mandate for an agent saved without one.
import {useState} from 'preact/hooks';
import {html} from '../html.js';
import {Icon} from '../ui/icons.js';
import {Creature} from '../creature/Creature.js';
import {FOCI, colorOf, freshSeed, lookFor} from '../creature/generate.js';
import {RuleField, ruleValid} from '../ui/RulesForm.js';
import {action} from '../api.js';
import {navigate} from '../router.js';
import {accountValue, money} from '../format.js';
import {activeAgents, connectedAccounts, accountLabel, takenColors} from './Agents.js';

const STEPS = ['Look and name', 'Model and account', 'Mandate'];

function Stepper({step}) {
  return html`<ol class="stepper" aria-label="Steps">
    ${STEPS.map((label, i) => {
      const n = i + 1, state = n < step ? 'done' : n === step ? 'current' : 'todo';
      return html`<li class=${'stepper-item ' + state} aria-current=${n === step ? 'step' : null}>
        <span class="stepper-mark">${state === 'done' ? html`<${Icon} name="check" />` : n}</span><span class="stepper-label">${label}</span>
      </li>`;
    })}
  </ol>`;
}

function ErrorNote({message}) {
  return message ? html`<div class="notice notice-error" role="alert"><${Icon} name="alert" /><span>${message}</span></div>` : null;
}

function LookStep({look, setLook, name, setName, taken, onNext}) {
  const color = colorOf(look);
  const valid = name.trim().length > 0 && name.length <= 100;
  return html`<form class="wizard-body" onSubmit=${e => { e.preventDefault(); if (valid) onNext(); }}>
    <div class="look-stage">
      <${Creature} look=${look} size=${168} track poke label=${name.trim() || 'Your new agent'} />
      <button type="button" class="btn" onClick=${() => setLook({...look, seed: freshSeed(taken)})}>Reroll</button>
      <span class="muted look-caption">${color.name}. This colour marks the agent everywhere in the app.</span>
    </div>
    <div class="wizard-fields">
      <label class="field"><span class="field-label">Name</span>
        <input class="input" value=${name} maxlength="100" autocomplete="off" placeholder="Give your agent a name" onInput=${e => setName(e.currentTarget.value)} />
      </label>
      <fieldset class="field">
        <legend class="field-label">Markets it focuses on</legend>
        <span>This picks its outfit. It does not limit what it can trade.</span>
        <div class="chips">
          ${FOCI.map(f => html`<label class="chip"><input type="radio" name="focus" value=${f.id} checked=${look.focus === f.id} onChange=${() => setLook({...look, focus: f.id})} /><span>${f.label}</span></label>`)}
        </div>
      </fieldset>
      <div class="wizard-actions">
        <a class="btn btn-ghost" href="/agents">Cancel</a>
        <button class="btn btn-primary" disabled=${!valid}>Continue</button>
      </div>
    </div>
  </form>`;
}

function ModelStep({workspace, draft, setDraft, onBack, onSaved, look, name}) {
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const providers = workspace.providers || {};
  const freeId = Object.keys(providers).find(id => providers[id].free);
  const accounts = connectedAccounts(workspace);
  const ownProviders = Object.entries(providers).filter(([, p]) => !p.free);
  const usingFree = draft.mode === 'free' && freeId;
  const provider = usingFree ? freeId : draft.provider;
  const models = providers[provider]?.models || [];
  const model = usingFree ? models[0] : draft.model;
  const ready = accounts.length && draft.accountId && provider && model && (usingFree || draft.key.trim());
  const set = patch => setDraft({...draft, ...patch});

  async function save(e) {
    e.preventDefault();
    if (!ready) return;
    setError('');
    setSaving(true);
    try {
      const result = await action('agent-save', {
        name: name.trim(), provider, model, accountId: draft.accountId, look,
        ...(usingFree ? {} : {modelKey: draft.key.trim()}),
      });
      set({key: ''});  // the key now lives encrypted on the server; do not keep it in the page
      if (result?.agentId) onSaved(result.agentId);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return html`<form class="wizard-body wizard-single" onSubmit=${save}>
    ${freeId ? html`<fieldset class="field">
      <legend class="field-label">Model</legend>
      <div class="choices">
        <label class="choice"><input type="radio" name="mode" checked=${draft.mode === 'free'} onChange=${() => set({mode: 'free'})} />
          <span><strong>${providers[freeId].shortLabel}</strong><small>${providers[freeId].models[0]} on Gemini. No API key needed, ${providers[freeId].dailyMessages} messages a day.</small></span></label>
        <label class="choice"><input type="radio" name="mode" checked=${draft.mode === 'own'} onChange=${() => set({mode: 'own'})} />
          <span><strong>Your own model</strong><small>OpenAI, Anthropic or Gemini with your API key.</small></span></label>
      </div>
    </fieldset>` : null}
    ${usingFree ? html`<p class="fine-print">${providers[freeId].notice}</p>` : html`<div class="field-row">
      <label class="field"><span class="field-label">Provider</span>
        <select class="input select" value=${draft.provider} onChange=${e => set({provider: e.currentTarget.value, model: ''})}>
          <option value="">Choose a provider</option>
          ${ownProviders.map(([id, p]) => html`<option value=${id}>${p.label}</option>`)}
        </select>
      </label>
      <label class="field"><span class="field-label">Model</span>
        <select class="input select" value=${draft.model} disabled=${!draft.provider} onChange=${e => set({model: e.currentTarget.value})}>
          <option value="">${draft.provider ? 'Choose a model' : 'Pick a provider first'}</option>
          ${(providers[draft.provider]?.models || []).map(m => html`<option value=${m}>${m}</option>`)}
        </select>
      </label>
    </div>
    <label class="field"><span class="field-label">API key</span>
      <input class="input" type="password" autocomplete="new-password" spellcheck="false" value=${draft.key} onInput=${e => set({key: e.currentTarget.value})} />
      <span>Saving checks this key and model with the provider. Nothing is generated, and the key is stored encrypted.</span>
    </label>`}
    <label class="field"><span class="field-label">Kalshi account</span>
      ${accounts.length ? html`<select class="input select" value=${draft.accountId} onChange=${e => set({accountId: e.currentTarget.value})}>
        ${accounts.map(a => html`<option value=${a.id}>${accountLabel(a)} · subaccount ${a.subaccount || 0}</option>`)}
      </select><span>An agent stays on the account it was created with.</span>`
      : html`<div class="notice notice-warn"><${Icon} name="info" /><span>Connect and test a Kalshi account first. <a href="/accounts">Connect Kalshi</a></span></div>`}
    </label>
    <${ErrorNote} message=${error} />
    <div class="wizard-actions">
      <button type="button" class="btn btn-ghost" onClick=${onBack}>Back</button>
      <button class="btn btn-primary" disabled=${!ready || saving}>${saving ? html`<span class="spinner" aria-hidden="true"></span>Checking the model` : 'Save agent and continue'}</button>
    </div>
  </form>`;
}

// Also the agent page's mandate editor: there onSaved closes the dialog and onCancel replaces "Finish later".
export function MandateStep({workspace, agent, onSaved, onCancel}) {
  const fields = workspace.rulesCatalog.mandateFields;
  const sliders = fields.filter(f => f.unit !== 'boolean');
  const toggle = fields.find(f => f.unit === 'boolean');
  const account = (workspace.accounts || []).find(a => a.id === agent.accountId);
  const base = accountValue(account?.balance);
  const [values, setValues] = useState(() => Object.fromEntries(fields.map(f => [f.key, agent.mandate?.[f.key] ?? f.start])));
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const ceiling = account?.effectiveCeiling;

  async function save(e) {
    e.preventDefault();
    if (sliders.some(f => !ruleValid(f, values[f.key]))) return;
    setError('');
    setSaving(true);
    try {
      const mandate = {...Object.fromEntries(sliders.map(f => [f.key, Number(values[f.key])])), [toggle.key]: !!values[toggle.key]};
      const result = await action('mandate-save', {id: agent.id, mandate});
      if (result?.agentId) { if (onSaved) onSaved(); else navigate('/agents'); }
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return html`<form class="wizard-body wizard-single" onSubmit=${save} novalidate>
    <div class="mandate-intro">
      <${Creature} look=${lookFor(agent)} size=${56} />
      <p>${agent.name}'s limits, as shares of its account's value. They apply on top of your trading rules.${ceiling
        ? ` Its daily cap has to fit this account's daily ceiling of ${ceiling.dailyPercent}%${base != null ? ` (${money(ceiling.dailyCap)})` : ''}.` : ''}</p>
    </div>
    ${sliders.map(f => html`<${RuleField} key=${f.key} rule=${f} value=${values[f.key]} base=${base}
      onChange=${(key, raw) => setValues(v => ({...v, [key]: raw}))} />`)}
    ${toggle ? html`<label class="switch-row">
      <span><strong>${toggle.label}</strong><small>${toggle.explanation}</small></span>
      <input class="switch" type="checkbox" role="switch" checked=${!!values[toggle.key]} onChange=${e => setValues(v => ({...v, [toggle.key]: e.currentTarget.checked}))} />
    </label>` : null}
    <${ErrorNote} message=${error} />
    <div class="wizard-actions">
      ${onCancel
        ? html`<button type="button" class="btn btn-ghost" onClick=${onCancel}>Cancel</button>`
        : html`<a class="btn btn-ghost" href="/agents">Finish later</a>`}
      <button class="btn btn-primary" disabled=${saving}>${saving ? html`<span class="spinner" aria-hidden="true"></span>Saving` : 'Save mandate'}</button>
    </div>
  </form>`;
}

export function NewAgent({workspace, query}) {
  const agents = activeAgents(workspace);
  const resumeId = query.get('agent');
  const resume = agents.find(a => a.id === resumeId);
  const [step, setStep] = useState(resume ? 3 : 1);
  const [agentId, setAgentId] = useState(resume?.id || null);
  const [look, setLook] = useState(() => ({seed: freshSeed(takenColors(workspace)), focus: 'general'}));
  const [name, setName] = useState('');
  const freeId = Object.keys(workspace.providers || {}).find(id => workspace.providers[id].free);
  const [draft, setDraft] = useState(() => ({
    mode: freeId ? 'free' : 'own', provider: '', model: '', key: '', accountId: connectedAccounts(workspace)[0]?.id || '',
  }));
  const agent = agents.find(a => a.id === agentId);

  if (resumeId && !resume) {
    return html`<div class="card"><div class="empty"><h3>Agent not found</h3><p>It may have been deleted.</p><a class="btn" href="/agents">Back to agents</a></div></div>`;
  }
  return html`<div class="wizard">
    <${Stepper} step=${step} />
    <section class="card">
      ${step === 1 ? html`<${LookStep} look=${look} setLook=${setLook} name=${name} setName=${setName} taken=${takenColors(workspace)} onNext=${() => setStep(2)} />` : null}
      ${step === 2 ? html`<${ModelStep} workspace=${workspace} draft=${draft} setDraft=${setDraft} look=${look} name=${name}
        onBack=${() => setStep(1)} onSaved=${id => { setAgentId(id); setStep(3); }} />` : null}
      ${step === 3 && agent ? html`<${MandateStep} workspace=${workspace} agent=${agent} />` : null}
    </section>
  </div>`;
}
