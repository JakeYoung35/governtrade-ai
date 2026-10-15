// The order drawer (?order=<id> on any page): the order, the governor's sizing, the agent's reasoning, approval when it
// is waiting, and every evaluation with its layers and checks. A modal <dialog>: Escape, Close, Back and a click
// outside all close it, and focus returns to what opened it.
import {useEffect, useRef} from 'preact/hooks';
import {html} from '../html.js';
import {navigate} from '../router.js';
import {money, when} from '../format.js';
import {statusOf} from '../governor-text.js';
import {GovernorRail} from './GovernorRail.js';
import {Verdict, verdictLabel} from './Verdict.js';
import {ApprovalCard, orderLine} from './ApprovalCard.js';
import {accountLabel} from '../screens/Agents.js';

const LAYERS = {account_ceiling: 'Your rules', agent_mandate: 'Agent mandate', execution: 'Execution'};
const layerName = layer => LAYERS[layer] || String(layer || '').replaceAll('_', ' ');

export function closeOrder() {
  if (history.state?.orderDrawer) { history.back(); return; }
  const query = new URLSearchParams(location.search);
  query.delete('order');
  navigate(location.pathname + (query.size ? `?${query}` : ''), {replace: true});
}

function Check({check}) {
  return html`<li>
    <div class="check-head"><strong>${check.name}</strong><${Verdict} value=${check.outcome} /></div>
    ${check.layer && layerName(check.layer).toLowerCase() !== String(check.name).toLowerCase() ? html`<span class="muted">${layerName(check.layer)}</span>` : null}
    ${check.reason ? html`<p>${check.reason}</p>` : null}
  </li>`;
}

// Checks that stopped or held the order come first; the ones that passed are folded away.
function Evaluation({evaluation, index, count}) {
  const checks = evaluation.checks || [];
  const flagged = checks.filter(c => c.outcome !== 'ALLOW');
  const passed = checks.filter(c => c.outcome === 'ALLOW');
  return html`<article class="evaluation">
    <div class="evaluation-head">
      <div><strong>${count > 1 ? `Check ${index + 1} of ${count}` : 'Checks'}</strong><span class="muted">${when(evaluation.time)}</span></div>
      <${Verdict} value=${evaluation.outcome} />
    </div>
    <ul class="layer-row" aria-label="Layers">
      ${Object.entries(evaluation.layers || {}).map(([layer, value]) => html`<li><span>${layerName(layer)}</span><${Verdict} value=${value} /></li>`)}
    </ul>
    ${flagged.length ? html`<ul class="checks">${flagged.map(check => html`<${Check} check=${check} />`)}</ul>` : null}
    ${passed.length ? html`<details class="checks-passed"><summary>${passed.length} check${passed.length === 1 ? '' : 's'} passed</summary>
      <ul class="checks">${passed.map(check => html`<${Check} check=${check} />`)}</ul></details>` : null}
  </article>`;
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
  const agent = workspace.agentConfigs?.find(a => a.id === order?.agent);
  const account = workspace.accounts?.find(a => a.id === order?.accountId);
  const status = statusOf(order?.status);
  const evaluations = order?.evaluations || [];
  const outside = e => {
    if (e.target !== dialog.current) return;
    const r = dialog.current.getBoundingClientRect();
    if (e.clientX < r.left || e.clientY < r.top || e.clientX > r.right || e.clientY > r.bottom) closeOrder();
  };

  return html`<dialog ref=${dialog} class="order-drawer" aria-labelledby="order-title" onCancel=${e => { e.preventDefault(); closeOrder(); }} onClick=${outside}>
    <div class="drawer-head"><h2 id="order-title">Order details</h2><button class="btn" onClick=${closeOrder} autofocus>Close</button></div>
    ${!order ? html`<div class="empty"><h3>Order unavailable</h3><p>This order is not in your workspace. It may belong to another account or have been removed.</p></div>` : html`<div class="drawer-body section-stack">
      <header class="order-title">
        <h3>${order.market?.name || order.market?.id}</h3>
        <p class="muted">${agent?.name || order.agent} · ${accountLabel(account)} · ${when(order.time)}</p>
        <div class="order-pills"><${Verdict} value=${order.decision} />${status.label !== verdictLabel(order.decision)
          ? html`<span class=${`pill pill-${status.tone}`}>${status.label}</span>` : null}</div>
      </header>
      <dl class="order-facts">
        <div><dt>Order</dt><dd class="num">${orderLine(order)}</dd></div>
        <div><dt>Actual cost</dt><dd class="num">${order.actualCost != null ? money(order.actualCost) : order.status === 'filled' ? '–' : 'Not filled'}</dd></div>
        <div><dt>Market</dt><dd class="mono-small">${order.market?.id || '–'}</dd></div>
        <div><dt>Order ID</dt><dd class="mono-small">${order.id}</dd></div>
      </dl>
      ${order.status === 'awaiting approval' ? html`<section class="section-stack-sm"><h3>Your approval</h3><${ApprovalCard} key=${order.id} order=${order} workspace=${workspace} detail /></section>`
        : html`<section class="section-stack-sm"><h3>Governor sizing</h3><${GovernorRail} order=${order} /></section>`}
      ${order.request ? html`<section class="section-stack-sm"><h3>Agent's reasoning</h3><p class="order-reason">${order.request}</p></section>` : null}
      <section class="section-stack-sm"><h3>Evaluations</h3>
        ${!evaluations.length ? html`<p class="muted">No evaluations recorded.</p>`
          : evaluations.map((evaluation, i) => html`<${Evaluation} evaluation=${evaluation} index=${i} count=${evaluations.length} />`)}
      </section>
    </div>`}
  </dialog>`;
}
