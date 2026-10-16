// Orders waiting for the user. Home shows the first few with a link here; this page shows them 20 at a time, so a
// backlog never turns into one endless page. The workspace state carries the first 50 waiting orders; past them this
// page reads the rest from the server.
import {useState} from 'preact/hooks';
import {html} from '../html.js';
import {pendingOrders} from '../order-state.js';
import {pageUrls, useReads} from '../remote.js';
import {ApprovalCard} from '../ui/ApprovalCard.js';

const HOME_LIMIT = 3;
const PAGE = 20;

export function Approvals({workspace, home = false}) {
  const [wanted, setWanted] = useState(home ? HOME_LIMIT : PAGE);
  const carried = pendingOrders(workspace);
  const count = workspace.orderStats?.awaiting ?? carried.length;
  const beyond = !home && wanted > carried.length && count > carried.length;
  const more = useReads(beyond ? pageUrls('/api/orders', {status: 'awaiting approval'}, wanted) : null, workspace);
  const orders = (beyond && more.data ? more.data.flatMap(p => p.orders) : carried).slice(0, wanted);
  if (home && !count) return null;
  const hidden = count - orders.length;
  return html`<section class="section-stack" aria-label="Approvals">
    <div class="section-heading"><h2>${home ? 'Waiting for you' : 'Pending approvals'} <span class="muted">${count}</span></h2>
      ${home && hidden > 0 ? html`<a href="/approvals">See all ${count}</a>` : home ? html`<a href="/approvals">Approvals</a>` : null}</div>
    ${orders.length ? orders.map(order => html`<${ApprovalCard} key=${order.id} order=${order} workspace=${workspace} verdict=${home} />`)
      : html`<div class="card empty"><h3>No approvals waiting</h3><p>Orders that need your review will appear here.</p></div>`}
    ${more.error ? html`<p class="notice notice-error" role="alert">${more.error.message}</p>` : null}
    ${!home && hidden > 0 ? html`<button class="btn activity-more" disabled=${more.loading} onClick=${() => setWanted(orders.length + PAGE)}>
      ${more.loading ? 'Loading…' : `Show ${Math.min(PAGE, hidden)} more`}</button>` : null}
  </section>`;
}
