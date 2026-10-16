// The trading rules form: one slider + number box per rule, with a live dollar preview. Used by first-login
// setup and by the Rules page. The catalog (rules, groups, always-on checks, defaults) comes from the server.
import {useState, useRef} from 'preact/hooks';
import {html} from '../html.js';
import {Icon} from './icons.js';
import {money, duration, workspaceValue} from '../format.js';
import {action} from '../api.js';

const UNIT = {percent: '%', seconds: 'sec', count: 'forecasts'};
const LOG_STEPS = 1000;

// Log-scaled rules (seconds, counts) use a 0..1000 slider so small values get as much travel as large ones.
export function toSlider(r, v) {
  if (r.scale !== 'log') return Number(v);
  const lo = Math.log(r.min), hi = Math.log(r.max);
  return Math.round((Math.log(Math.min(r.max, Math.max(r.min, Number(v) || r.min))) - lo) / (hi - lo) * LOG_STEPS);
}

export function fromSlider(r, t) {
  if (r.scale !== 'log') return Number(t);
  const v = Math.exp(Math.log(r.min) + Number(t) / LOG_STEPS * (Math.log(r.max) - Math.log(r.min)));
  const step = r.step || 1;
  return Math.min(r.max, Math.max(r.min, Math.round(v / step) * step));
}

export function ruleShow(r, v) {
  if (r.unit === 'percent') return `${v}%`;
  if (r.unit === 'seconds') return duration(v);
  return `${v} forecast${Number(v) === 1 ? '' : 's'}`;
}

// What a value means in dollars for this workspace (or a $1,000 example account when no balance is known).
export function rulePreview(r, value, base, example) {
  const v = Number(value);
  const usd = p => money(p / 100 * base);
  const acct = example ? 'a $1,000 example account' : `your ${money(base)} account`;
  return {
    kellyMultiplier: `If full Kelly says to bet ${money(base * .1)}, the governor bets ${money(base * .1 * v / 100)}.`,
    marketCapPercent: `At most ${usd(v)} of ${acct} at risk in one market.`,
    agentCapPercent: `At most ${usd(v)} of ${acct} at risk per agent.`,
    eventCapPercent: `At most ${usd(v)} of ${acct} at risk on one event.`,
    totalCapPercent: `At most ${usd(v)} of ${acct} at risk in total; the rest stays in cash.`,
    dailySpendingPercent: `Agents can spend up to ${usd(v)} of ${acct} a day in total.`,
    dailyLossPercent: `New trades stop after a ${usd(v)} drop from today's start.`,
    quoteMaxAgeSeconds: `Prices older than ${duration(v)} are refused.`,
    proposalLifetimeSeconds: `Proposals expire after ${duration(v)}.`,
    approvalWindowSeconds: `Trades wait up to ${duration(v)} for your approval.`,
    startingTrustPercent: v === 50 ? 'New agents start neutral.' : v < 50 ? 'New agents bet less until they prove themselves.' : 'New agents bet more from the start.',
    trustWeight: `After ${v} scored forecast${v === 1 ? '' : 's'}, an agent's own record counts as much as the starting trust.`,
    autoLimitPercent: `Trades up to ${usd(v)} run automatically; larger ones wait for you.`,
    dailyCapPercent: `This agent spends at most ${usd(v)} a day.`,
    minimumPercent: `At least ${usd(v)} stays in cash after this agent's trades.`,
  }[r.key] || '';
}

export const ruleValid = (r, raw) => {
  const v = Number(raw);
  return String(raw).trim() !== '' && Number.isFinite(v) && v >= r.min && v <= r.max && (!r.integer || Number.isInteger(v));
};

export function RuleField({rule: r, value, base, onChange}) {
  const log = r.scale === 'log';
  const [lastGood, setLastGood] = useState(ruleValid(r, value) ? Number(value) : r.start ?? r.min);
  const valid = ruleValid(r, value);
  const shown = valid ? Number(value) : lastGood;
  const min = log ? 0 : r.min, max = log ? LOG_STEPS : r.max, pos = toSlider(r, shown);
  const fill = `${(pos - min) / (max - min) * 100}%`;
  const id = `rule-${r.key}`;
  const set = raw => {
    if (ruleValid(r, raw)) setLastGood(Number(raw));
    onChange(r.key, raw);
  };
  return html`<div class="rule">
    <div class="rule-head">
      <label for=${id}>${r.label}</label>
      <div class="rule-number" aria-invalid=${valid ? null : 'true'}>
        <input id=${id} name=${r.key} type="number" inputmode="decimal" min=${r.min} max=${r.max} step=${r.integer ? 1 : 'any'}
          value=${value} aria-describedby=${`${id}-preview`} onInput=${e => set(e.currentTarget.value)} />
        <span>${UNIT[r.unit]}</span>
      </div>
    </div>
    <input class="rule-slider" type="range" min=${min} max=${max} step=${log ? 1 : r.step} value=${pos} style=${`--fill:${fill}`}
      aria-label=${r.label} aria-valuetext=${ruleShow(r, shown)} onInput=${e => set(fromSlider(r, e.currentTarget.value))} />
    <div class="rule-scale" aria-hidden="true"><span>${ruleShow(r, r.min)}</span><span>${ruleShow(r, r.max)}</span></div>
    ${valid ? null : html`<p class="rule-error" role="alert">Enter a number from ${ruleShow(r, r.min)} to ${ruleShow(r, r.max)}${r.integer ? ', with no decimals' : ''}.</p>`}
    <p class="rule-preview" id=${`${id}-preview`}>${rulePreview(r, shown, base ?? 1000, base == null)}</p>
    <p class="rule-explain">${r.explanation}</p>
  </div>`;
}

export function RulesForm({workspace, setup, onSaved}) {
  const catalog = workspace.rulesCatalog;
  const start = workspace.rules?.values || workspace.rules?.previous || catalog.defaults;
  const [values, setValues] = useState(() => Object.fromEntries(catalog.rules.map(r => [r.key, start[r.key] ?? r.start])));
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const form = useRef(null);
  const base = workspaceValue(workspace);

  async function submit(e) {
    e.preventDefault();
    const bad = catalog.rules.find(r => !ruleValid(r, values[r.key]));
    if (bad) {
      form.current.elements[bad.key].focus();
      return;
    }
    setError('');
    setSaving(true);
    try {
      const result = await action('rules-save', {values: Object.fromEntries(catalog.rules.map(r => [r.key, Number(values[r.key])]))});
      if (result) onSaved?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return html`<form ref=${form} onSubmit=${submit} novalidate>
    ${catalog.groups.map(g => html`<section class="card rules-group" aria-labelledby=${`group-${g.id}`}>
      <h2 id=${`group-${g.id}`}>${g.title}</h2>
      <p>${g.intro}</p>
      ${catalog.rules.filter(r => r.group === g.id).map(r => html`<${RuleField} key=${r.key} rule=${r} value=${values[r.key]} base=${base}
        onChange=${(key, raw) => setValues(v => ({...v, [key]: raw}))} />`)}
    </section>`)}
    <section class="card rules-group" aria-labelledby="group-always-on">
      <h2 id="group-always-on">Always on</h2>
      <p>These checks protect your data and your orders, so they cannot be switched off.</p>
      <div class="always-on-list">
        ${catalog.alwaysOn.map(c => html`<div class="always-on"><span class="check"><${Icon} name="check" /></span><div><strong>${c.label}</strong><small>${c.explanation}</small></div></div>`)}
      </div>
    </section>
    <div class="rules-save">
      ${error ? html`<div class="notice notice-error" role="alert"><${Icon} name="alert" />${error}</div>` : null}
      <button class="btn btn-primary btn-lg btn-block" disabled=${saving}>
        ${saving ? html`<span class="spinner" aria-hidden="true"></span>Saving` : setup ? 'Save my rules and continue' : 'Save rules'}
      </button>
      <small>${setup
        ? 'Each rule starts at a suggested position. Slide to adjust, or save to accept. You can change them later under Rules.'
        : `Version ${workspace.rules.version} · saving creates a new version; decisions already made keep the version they used.`}</small>
    </div>
  </form>`;
}
