import {html} from '../html.js';
import {money} from '../format.js';
function Figures({summary = {}}) {
  return html`<dl class="money-strip">
    <div><dt>Account value</dt><dd class="num">${money(summary.accountValue)}</dd></div>
    <div><dt>Spent today</dt><dd class="num">${money(summary.spentToday)} <small>/ ${money(summary.dailyCap)}</small></dd></div>
    <div><dt>At risk</dt><dd class="num">${money(summary.atRisk)} <small>/ ${money(summary.exposureCap)}</small></dd></div>
    <div><dt>Loss-limit headroom</dt><dd class="num">${money(summary.lossHeadroom)}</dd></div>
  </dl>`;
}
export function MoneyStrip({workspace}) {
  const accounts = (workspace.accounts || []).filter(a => !['removed', 'disconnected'].includes(a.status));
  return html`<section class="card" aria-label="Workspace money">
    <div class="card-head"><h2>Workspace totals</h2><span class="muted">Rules v${workspace.rules?.version || 0}</span></div>
    <${Figures} summary=${workspace.summary} />
    ${workspace.summary?.breakerLatched ? html`<p class="notice notice-warn">A loss limit has been reached. Trading is stopped for the affected account.</p>` : null}
    <details class="account-breakdown"><summary>Accounts · ${accounts.map(a => `${a.name} (${a.environment === 'live' ? 'Live' : 'Demo'})`).join(', ') || 'None connected'}</summary>
      ${accounts.map(a => html`<section><h3>${a.name} · ${a.environment === 'live' ? 'Live' : 'Demo'}</h3><${Figures} summary=${a.summary || {}} />${a.stale || a.error ? html`<p class="notice notice-warn">${a.error || 'Balance is stale. Trades wait for a fresh balance.'}</p>` : null}</section>`)}
    </details>
    ${workspace.summary?.stale ? html`<p class="notice notice-warn">Some balances are stale. Review the account details.</p>` : null}
  </section>`;
}
