import {useEffect, useState} from 'preact/hooks';
import {html} from '../html.js';
import {money} from '../format.js';
import {countdown} from '../order-state.js';
import {action} from '../api.js';
import {useStore, setStore, getStore} from '../store.js';
import {GovernorRail} from './GovernorRail.js';
import {Verdict} from './Verdict.js';
export function orderHref(id) {
  const query = new URLSearchParams(location.search); query.set('order', id);
  return `${location.pathname}?${query}`;
}
export function ApprovalCard({order, workspace, detail = false}) {
  const [now, setNow] = useState(Date.now());
  const [error, setError] = useState('');
  const {busy} = useStore();
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  const expiry = countdown(order.governedTerms?.approveBy, now);
  const agent = workspace.agentConfigs?.find(a => a.id === order.agent);
  const account = workspace.accounts?.find(a => a.id === order.accountId);
  async function decide(approve) {
    setError('');
    try {
      await action('decision', {id: order.id, approve});
      const latest = getStore().workspace?.orders?.find(o => o.id === order.id);
      if (latest) setStore({toast: `Order ${latest.status}.`});
    } catch (e) { setError(e.message); }
  }
  return html`<article class="card approval-card">
    <div class="approval-heading"><div><h3>${order.market?.name || order.market?.id || 'Market'}</h3><p class="muted">${agent?.name || order.agent} · ${account?.name || 'Account'} · ${account?.environment === 'live' ? 'Live' : 'Demo'}</p></div><${Verdict} value=${order.decision || 'REQUIRE_APPROVAL'} /></div>
    <p><strong class="num">${money(order.amount)}</strong> · ${order.direction}</p>
    <${GovernorRail} order=${order} />
    ${order.request ? html`<p class="order-reason">${order.request}</p>` : null}
    <div class="approval-actions">
      <button class="btn btn-primary" disabled=${busy || expiry.expired} onClick=${() => decide(true)}>Approve</button>
      <button class="btn" disabled=${busy} onClick=${() => decide(false)}>Deny</button>
      ${!detail ? html`<a class="btn btn-ghost" href=${orderHref(order.id)}>View details</a>` : null}
      <span class="num muted" role="timer">${expiry.label}</span>
    </div>
    <p class="muted">Approval rechecks current limits, cash, agent status and quotes.</p>
    ${error ? html`<p class="notice notice-error" role="alert">${error}</p>` : null}
  </article>`;
}
