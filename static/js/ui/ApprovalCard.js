// One order waiting for the user: what the agent asked for, how the governor sized it, and Approve or Deny before the
// approval deadline (governedTerms.approveBy). A late approval is cancelled by the server, so Approve stops at zero.
// In the order drawer (detail) the drawer already shows the market and the reasoning, so the card shows only the rest.
import {useEffect, useState} from 'preact/hooks';
import {html} from '../html.js';
import {money} from '../format.js';
import {countdown} from '../order-state.js';
import {action, fetchData} from '../api.js';
import {useStore, setStore} from '../store.js';
import {statusOf} from '../governor-text.js';
import {GovernorRail} from './GovernorRail.js';
import {Verdict} from './Verdict.js';

export function orderHref(id) {
  const query = new URLSearchParams(location.search);
  query.set('order', id);
  return `${location.pathname}?${query}`;
}

// "Buy YES · $49.68": the side and the order's budget, as the agent and the chat say it.
export const orderLine = order => `Buy ${String(order.direction || '').toUpperCase()} · ${money(order.amount)}`;

// Reasoning can run to 8,000 characters: three lines in a card, the rest on request (and always in full in the drawer).
// The workspace state carries only the first few hundred characters (requestLength is the full length), so Show all
// reads the full order the first time.
function Reasoning({order}) {
  const [open, setOpen] = useState(false);
  const [full, setFull] = useState(null);
  const [state, setState] = useState({loading: false, error: ''});
  const text = full ?? order.request;
  const cut = (order.requestLength || 0) > order.request.length;
  const long = cut || text.length > 240 || text.split('\n').length > 3;
  async function toggle() {
    if (open || full != null || !cut) { setOpen(!open); return; }
    setState({loading: true, error: ''});
    try {
      const d = await fetchData(`/api/orders/${encodeURIComponent(order.id)}`);
      if (d) { setFull(d.order.request || ''); setOpen(true); }
      setState({loading: false, error: ''});
    } catch (e) {
      setState({loading: false, error: e.message});
    }
  }
  return html`<div class="order-reason-block">
    <p class=${'order-reason' + (long && !open ? ' clamped' : '')}><span class="muted">Agent's reasoning: </span>${text}</p>
    ${long ? html`<button type="button" class="link-button" aria-expanded=${open} disabled=${state.loading} onClick=${toggle}>${state.loading ? 'Loading…' : open ? 'Show less' : 'Show all'}</button>` : null}
    ${state.error ? html`<p class="notice notice-error" role="alert">${state.error}</p>` : null}
  </div>`;
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
      // The decided order leaves the waiting list, and may be outside the state's newest orders, so its status comes
      // from the action's result (null for a denial; undefined when the user changed meanwhile).
      const result = await action('decision', {id: order.id, approve});
      if (result === undefined) return;
      setStore({toast: !approve ? 'Denied. The order will not be placed.' : result?.status ? `Approved: ${statusOf(result.status).label.toLowerCase()}.` : 'Approved.'});
    } catch (e) {
      setError(e.message);
    }
  }

  return html`<article class=${'approval-card' + (detail ? ' approval-card-detail' : ' card')}>
    ${detail ? null : html`<div class="approval-heading">
      <div><h3>${order.market?.name || order.market?.id || 'Market'}</h3>
        <p class="muted">${agent?.name || order.agent} · ${account?.name || 'Account'}${account?.environment === 'live' ? ' · Live money' : ''}</p></div>
      <${Verdict} value=${order.decision || 'REQUIRE_APPROVAL'} />
    </div>`}
    ${detail ? null : html`<p class="approval-line">${orderLine(order)}</p>`}
    <${GovernorRail} order=${order} />
    ${!detail && order.request ? html`<${Reasoning} order=${order} />` : null}
    <div class="approval-actions">
      <button class="btn btn-primary" disabled=${busy || expiry.expired} onClick=${() => decide(true)}>Approve</button>
      <button class="btn" disabled=${busy} onClick=${() => decide(false)}>Deny</button>
      ${!detail ? html`<a class="btn btn-ghost" href=${orderHref(order.id)}>Details</a>` : null}
      <span class=${'approval-timer' + (expiry.expired ? ' expired' : ' num')} role="timer" aria-live="off">${expiry.label === 'Expired' ? 'Expired: too late to approve' : expiry.label}</span>
    </div>
    <p class="muted approval-note">Approving rechecks your limits, cash, the agent's status and the price before the order is placed.</p>
    ${error ? html`<p class="notice notice-error" role="alert">${error}</p>` : null}
  </article>`;
}
