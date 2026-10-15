// Everything that happened: headline counts, the daily outcomes chart, every order with search and filters, and the
// workspace's events. Merges the old Governance and Monitoring pages. Pause all agents is here as an emergency stop.
import {useState} from 'preact/hooks';
import {html} from '../html.js';
import {Icon} from '../ui/icons.js';
import {action} from '../api.js';
import {setStore, useStore} from '../store.js';
import {when} from '../format.js';
import {filterOrders, activityCounts, dailyOutcomes, STATUS_FILTERS, CATEGORIES, DECISIONS} from '../activity.js';
import {statusOf, eventKind} from '../governor-text.js';
import {DecisionsTable} from '../ui/DecisionsTable.js';
import {orderHref} from '../ui/ApprovalCard.js';

const PAGE = 50;
const NO_FILTERS = {query: '', status: '', agent: '', category: '', decision: ''};
const SERIES = [['filled', 'Filled'], ['review', 'Needed your approval'], ['blocked', 'Blocked or denied']];
const dayLabel = day => new Date(`${day}T12:00:00`).toLocaleDateString('en-US', {month: 'short', day: 'numeric'});  // a local day
const capitalised = text => text.charAt(0).toUpperCase() + text.slice(1);

function Counts({orders}) {
  const c = activityCounts(orders);
  const items = [['Requested', c.requested, 'Every proposal the governor saw'], ['Ran automatically', c.automatic, 'Filled within your limits'],
    ['Waiting for you', c.awaiting, 'Approve or deny under Approvals'], ['Blocked or denied', c.blocked, 'Stopped by a rule or by you']];
  return html`<section class="card"><dl class="money-strip">
    ${items.map(([label, n, note]) => html`<div><dt>${label}</dt><dd class="num">${n}</dd><small>${note}</small></div>`)}
  </dl></section>`;
}

// Grouped bars, one group per day. Status colours cannot be told apart by colour alone (red/amber under deuteranopia),
// so each series also has a fixed position, a legend, hatching for "blocked" and a table view.
function OutcomesChart({orders}) {
  const days = dailyOutcomes(orders);
  const max = Math.max(1, ...days.flatMap(d => SERIES.map(([key]) => d[key])));
  return html`<section class="card">
    <div class="card-head"><h2>Daily outcomes</h2><span class="muted chart-scope">${days.length ? `Last ${days.length} day${days.length === 1 ? '' : 's'} with proposals` : ''}</span></div>
    <div class="card-body">
      ${!days.length ? html`<p class="muted">The chart fills in as your agents propose trades.</p>` : html`
        <ul class="chart-legend">${SERIES.map(([key, label]) => html`<li><i class=${`swatch outcome-${key}`} aria-hidden="true"></i>${label}</li>`)}</ul>
        <div class="outcome-chart" role="img" aria-label="Daily outcomes. The table under the chart lists the numbers.">
          ${days.map(d => html`<div class="outcome-day">
            <div class="outcome-bars">${SERIES.map(([key, label]) => html`<span class=${`outcome-bar outcome-${key}`} style=${`--h:${d[key] / max * 100}%`}
              data-tip=${`${dayLabel(d.day)}: ${d[key]} ${label.toLowerCase()}`}></span>`)}</div>
            <span class="outcome-label">${dayLabel(d.day)}</span>
          </div>`)}
        </div>
        <details class="chart-table"><summary>Show as a table</summary>
          <table class="decisions-table"><thead><tr><th scope="col">Day</th>${SERIES.map(([, label]) => html`<th scope="col">${label}</th>`)}</tr></thead>
            <tbody>${days.map(d => html`<tr><td>${dayLabel(d.day)}</td>${SERIES.map(([key]) => html`<td class="num">${d[key]}</td>`)}</tr>`)}</tbody></table>
        </details>
        <p class="fine-print">An order you approved that then filled counts in both.</p>`}
    </div>
  </section>`;
}

function Filters({filters, update, clear, workspace, matching, total}) {
  const select = (label, key, options) => html`<label class="field"><span class="field-label">${label}</span>
    <select class="input select" value=${filters[key]} onChange=${e => update(key, e.currentTarget.value)}>
      <option value="">All</option>${options.map(([value, text]) => html`<option value=${value}>${text}</option>`)}
    </select></label>`;
  const active = Object.values(filters).some(Boolean);
  return html`<section class="card card-body activity-filters-card">
    <div class="activity-filters">
      <label class="field activity-search"><span class="field-label">Search</span>
        <input class="input" type="search" placeholder="Market, agent, order ID or reasoning" value=${filters.query} onInput=${e => update('query', e.currentTarget.value)} /></label>
      ${select('Status', 'status', STATUS_FILTERS.map(s => [s, statusOf(s).label]))}
      ${select('Agent', 'agent', (workspace.agentConfigs || []).map(a => [a.id, a.status === 'deleted' ? `${a.name} (deleted)` : a.name]))}
      ${select('Category', 'category', CATEGORIES.map(c => [c, capitalised(c)]))}
      ${select('Verdict', 'decision', DECISIONS)}
    </div>
    <div class="activity-filters-foot" role="status">
      <span class="muted">${active ? `${matching} of ${total} orders match` : `${total} order${total === 1 ? '' : 's'}`}</span>
      ${active ? html`<button class="btn btn-ghost btn-sm" onClick=${clear}>Clear filters</button>` : null}
    </div>
  </section>`;
}

function Events({events}) {
  const [shown, setShown] = useState(20);
  return html`<section class="card">
    <div class="card-head"><h2>Events</h2></div>
    ${!events.length ? html`<div class="empty"><p>No events yet. Connections, rule changes and order results appear here.</p></div>` : html`
      <ol class="event-list">${events.slice(0, shown).map(e => html`<li class="event-row" key=${e.id}>
        <div class="event-main"><p>${e.text}</p><span class="muted">${when(e.time)}</span></div>
        <div class="event-side"><span class="pill pill-neutral">${eventKind(e.kind)}</span>${e.trade ? html`<a href=${orderHref(e.trade)}>Order</a>` : null}</div>
      </li>`)}</ol>
      ${events.length > shown ? html`<div class="card-foot"><button class="btn btn-sm" onClick=${() => setShown(shown + 20)}>Show ${Math.min(20, events.length - shown)} more</button></div>` : null}`}
  </section>`;
}

export function Activity({workspace}) {
  const {busy} = useStore();
  const [filters, setFilters] = useState(NO_FILTERS);
  const [limit, setLimit] = useState(PAGE);
  const orders = workspace.orders || [];
  const agents = new Map((workspace.agentConfigs || []).map(a => [a.id, a]));
  const filtered = filterOrders(orders, filters, agents);
  const running = workspace.permissions?.automation !== false;
  const update = (key, value) => { setFilters(f => ({...f, [key]: value})); setLimit(PAGE); };

  async function pauseAll() {
    try {
      await action('permissions', {values: {automation: false}});
      setStore({toast: 'All agents paused. Turn trading back on under Rules.'});
    } catch (e) {
      setStore({error: e.message});
    }
  }

  return html`<div class="section-stack">
    <div class="page-head">
      <p>Every order your agents proposed, what the governor decided, and what happened in your workspace.</p>
      ${running ? html`<button class="btn btn-danger-outline" disabled=${busy} onClick=${pauseAll}>Pause all agents</button>`
        : html`<a class="btn" href="/rules"><${Icon} name="rules" />Trading is paused · Rules</a>`}
    </div>
    <${Counts} orders=${orders} />
    <${OutcomesChart} orders=${orders} />
    <${Filters} filters=${filters} update=${update} clear=${() => { setFilters(NO_FILTERS); setLimit(PAGE); }} workspace=${workspace} matching=${filtered.length} total=${orders.length} />
    <${DecisionsTable} workspace=${workspace} orders=${filtered} limit=${limit} title="Orders" activity
      empty=${orders.length ? 'No orders match these filters.' : "No orders yet. Your agents' proposals will appear here."} />
    ${filtered.length > limit ? html`<button class="btn activity-more" onClick=${() => setLimit(limit + PAGE)}>Show ${Math.min(PAGE, filtered.length - limit)} more orders</button>` : null}
    <${Events} events=${workspace.events || []} />
  </div>`;
}
