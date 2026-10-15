import {html} from '../html.js';
import {pendingOrders} from '../order-state.js';
import {ApprovalCard} from '../ui/ApprovalCard.js';
export function Approvals({workspace, home = false}) {
  const orders = pendingOrders(workspace);
  if (home && !orders.length) return null;
  return html`<section class="section-stack" aria-label="Approvals">
    <div class="section-heading"><h2>${home ? 'Waiting for you' : 'Pending approvals'} <span class="muted">${orders.length}</span></h2>${home ? html`<a href="/approvals">All approvals</a>` : null}</div>
    ${orders.length ? orders.map(order => html`<${ApprovalCard} key=${order.id} order=${order} workspace=${workspace} />`) : html`<div class="card empty"><h3>No approvals waiting</h3><p>Orders that need your review will appear here.</p></div>`}
  </section>`;
}
