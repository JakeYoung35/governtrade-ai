import {html} from '../html.js';
import {money} from '../format.js';
import {reasonText as label} from '../governor-text.js';
export function GovernorRail({order}) {
  const g = order.governor;
  if (!g) return html`<p class="muted">Governor sizing details are unavailable for this order.</p>`;
  return html`<div class="governor-detail">
    <ol class="governor-rail" aria-label="Governor sizing">
      ${[['Asked', g.maxBudgetUsd], ['Kelly target', g.targetStake], ['After caps', g.stakeLimit], ['Approved budget', g.approvedBudgetUsd]].map(([title, amount]) => html`<li><span>${title}</span><strong class="num">${money(amount)}</strong></li>`)}
    </ol>
    <p class="muted">${g.decision ? label(g.decision) : 'Pending decision'}${g.reasonCodes?.length ? ` · ${g.reasonCodes.map(label).join(', ')}` : ''}</p>
    ${g.headroom ? html`<details><summary>Cap headroom</summary><dl class="detail-grid">${Object.entries(g.headroom).map(([cap, value]) => html`<div><dt>${label(cap)}</dt><dd class="num">${money(value)}</dd></div>`)}</dl></details>` : null}
  </div>`;
}
