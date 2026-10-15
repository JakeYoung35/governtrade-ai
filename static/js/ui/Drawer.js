import {useEffect, useRef} from 'preact/hooks';
import {html} from '../html.js';
import {navigate} from '../router.js';
import {money, when} from '../format.js';
import {GovernorRail} from './GovernorRail.js';
import {Verdict} from './Verdict.js';
import {ApprovalCard} from './ApprovalCard.js';
export function closeOrder() {
  if (history.state?.orderDrawer) { history.back(); return; }
  const query = new URLSearchParams(location.search); query.delete('order');
  navigate(location.pathname + (query.size ? `?${query}` : ''), {replace: true});
}
export function Drawer({id, workspace}) {
  const dialog = useRef(null);
  useEffect(() => {
    const previous = document.activeElement;
    const oldOverflow = document.body.style.overflow;
    dialog.current.showModal();
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = oldOverflow; if (previous?.isConnected) previous.focus(); };
  }, []);
  const order = workspace.orders?.find(o => o.id === id);
  return html`<dialog ref=${dialog} class="order-drawer" aria-labelledby="order-title" onCancel=${e => {e.preventDefault(); closeOrder();}} onClick=${e => {if (e.target === dialog.current) {const r = dialog.current.getBoundingClientRect(); if (e.clientX < r.left || e.clientY < r.top || e.clientX > r.right || e.clientY > r.bottom) closeOrder();}}}>
    <div class="drawer-head"><h2 id="order-title">Order details</h2><button class="btn" onClick=${closeOrder} autofocus>Close</button></div>
    ${!order ? html`<div class="empty"><h3>Order unavailable</h3><p>This order is not in the current workspace.</p></div>` : html`<div class="section-stack drawer-body">
      <div><h3>${order.market?.name || order.market?.id}</h3><p class="muted">${order.id} · ${when(order.time)}</p></div>
      <dl class="detail-grid"><div><dt>Status</dt><dd>${order.status}</dd></div><div><dt>Direction</dt><dd>${order.direction}</dd></div><div><dt>Requested order</dt><dd class="num">${money(order.amount)}</dd></div><div><dt>Actual cost</dt><dd class="num">${money(order.actualCost)}</dd></div></dl>
      ${order.status === 'awaiting approval' ? html`<${ApprovalCard} key=${order.id} order=${order} workspace=${workspace} detail />` : html`<${GovernorRail} order=${order} /><p class="order-reason">${order.request}</p>`}
      <section class="section-stack"><h3>Evaluations and checks</h3>
        ${!(order.evaluations || []).length ? html`<p class="muted">No evaluations recorded.</p>` : (order.evaluations || []).map((evaluation, i) => html`<article class="evaluation"><div class="section-heading"><strong>Evaluation ${i + 1}</strong><${Verdict} value=${evaluation.outcome} /></div><p class="muted">${when(evaluation.time)}</p>
          <dl class="detail-grid">${Object.entries(evaluation.layers || {}).map(([name, value]) => html`<div><dt>${name.replaceAll('_', ' ')}</dt><dd><${Verdict} value=${value} /></dd></div>`)}</dl>
          <ul class="checks">${(evaluation.checks || []).map(check => html`<li><div class="section-heading"><strong>${check.name}</strong><${Verdict} value=${check.outcome} /></div><p class="muted">${check.layer?.replaceAll('_', ' ')}</p><p>${check.reason}</p></li>`)}</ul>
        </article>`)}
      </section>
    </div>`}
  </dialog>`;
}
