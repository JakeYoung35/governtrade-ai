import {html} from '../html.js';
import {money, when} from '../format.js';
import {Creature} from '../creature/Creature.js';
import {lookFor} from '../creature/generate.js';
import {Verdict} from './Verdict.js';
import {orderHref} from './ApprovalCard.js';
export function DecisionsTable({workspace, limit = 12, title = 'Recent decisions', activity = false}) {
  const agents = new Map((workspace.agentConfigs || []).map(a => [a.id, a]));
  const orders = (workspace.orders || []).slice(0, limit);
  return html`<section class="card"><div class="card-head"><h2>${title}</h2>${!activity ? html`<a href="/activity">All activity</a>` : null}</div>
    ${!orders.length ? html`<div class="empty"><p>No decisions yet. Your agents' orders will appear here.</p></div>` : html`<div class="table-scroll" tabindex="0" role="region" aria-label="Recent decisions"><table class="decisions-table"><thead><tr><th scope="col">Market / agent</th><th scope="col">Amount</th><th scope="col">Verdict</th><th scope="col">Status</th><th scope="col">Time</th></tr></thead><tbody>
      ${orders.map(o => {const agent = agents.get(o.agent) || {id: o.agent, name: o.agent}; return html`<tr key=${o.id}><td><a href=${orderHref(o.id)}>${o.market?.name || o.market?.id}</a><span class="table-agent"><${Creature} look=${lookFor(agent)} size=${20} />${agent.name} · ${o.direction}</span></td><td class="num">${money(o.amount)}</td><td><${Verdict} value=${o.decision} /></td><td>${o.status}</td><td>${when(o.time)}</td></tr>`;})}
    </tbody></table></div>`}
  </section>`;
}
