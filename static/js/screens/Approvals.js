// Orders waiting for the user. Home shows the first few with a link here; this page shows them 20 at a time, so a
// backlog never turns into one endless page.
import {useState} from 'preact/hooks';
import {html} from '../html.js';
import {pendingOrders} from '../order-state.js';
import {ApprovalCard} from '../ui/ApprovalCard.js';

const HOME_LIMIT = 3;
const PAGE = 20;

export function Approvals({workspace, home = false}) {
  const [limit, setLimit] = useState(home ? HOME_LIMIT : PAGE);
  const orders = pendingOrders(workspace);
  if (home && !orders.length) return null;
  const hidden = orders.length - limit;
  return html`<section class="section-stack" aria-label="Approvals">
    <div class="section-heading"><h2>${home ? 'Waiting for you' : 'Pending approvals'} <span class="muted">${orders.length}</span></h2>
      ${home && hidden > 0 ? html`<a href="/approvals">See all ${orders.length}</a>` : home ? html`<a href="/approvals">Approvals</a>` : null}</div>
    ${orders.length ? orders.slice(0, limit).map(order => html`<${ApprovalCard} key=${order.id} order=${order} workspace=${workspace} />`)
      : html`<div class="card empty"><h3>No approvals waiting</h3><p>Orders that need your review will appear here.</p></div>`}
    ${!home && hidden > 0 ? html`<button class="btn activity-more" onClick=${() => setLimit(limit + PAGE)}>Show ${Math.min(PAGE, hidden)} more</button>` : null}
  </section>`;
}
