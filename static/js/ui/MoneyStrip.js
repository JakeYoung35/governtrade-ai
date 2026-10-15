// Home's money at a glance: the workspace totals from the governor's read-only summary, and the same per account.
// Unknown figures stay as dashes; totals are never silently partial (Workspace.public sends null instead).
import {html} from '../html.js';
import {Icon} from './icons.js';
import {money} from '../format.js';
import {accountLabel} from '../screens/Agents.js';

function Figures({summary = {}}) {
  return html`<dl class="money-strip">
    <div><dt>Account value</dt><dd class="num">${money(summary.accountValue)}</dd></div>
    <div><dt>Spent today</dt><dd class="num">${money(summary.spentToday)}</dd><small>of ${money(summary.dailyCap)} a day</small></div>
    <div><dt>At risk</dt><dd class="num">${money(summary.atRisk)}</dd><small>of ${money(summary.exposureCap)} allowed</small></div>
    <div><dt>Loss limit headroom</dt><dd class="num">${money(summary.lossHeadroom)}</dd><small>before new trades stop</small></div>
  </dl>`;
}

const Warning = ({children}) => html`<div class="notice notice-warn"><${Icon} name="alert" /><span>${children}</span></div>`;

export function MoneyStrip({workspace}) {
  const accounts = (workspace.accounts || []).filter(a => !['removed', 'disconnected'].includes(a.status));
  const summary = workspace.summary || {};
  const notes = [
    summary.breakerLatched ? 'A loss limit was reached, so new trades are stopped for the affected account.' : null,
    summary.stale ? 'Some balances are out of date. Trades on those accounts wait for a fresh balance.' : null,
  ].filter(Boolean);
  return html`<section class="card" aria-label="Workspace money">
    <div class="card-head"><h2>Workspace totals</h2><span class="muted">Rules v${workspace.rules?.version || 0}</span></div>
    <${Figures} summary=${summary} />
    ${notes.length ? html`<div class="card-notes">${notes.map(n => html`<${Warning}>${n}</${Warning}>`)}</div>` : null}
    <details class="account-breakdown"><summary>${accounts.length === 1 ? `1 account: ${accountLabel(accounts[0])}` : accounts.length ? `${accounts.length} accounts` : 'No connected accounts'}</summary>
      ${accounts.map(a => html`<section><h3>${accountLabel(a)}</h3><${Figures} summary=${a.summary || {}} />
        ${a.stale || a.error ? html`<${Warning}>${a.error || 'This balance is out of date. Trades wait for a fresh balance.'}</${Warning}>` : null}</section>`)}
    </details>
  </section>`;
}
