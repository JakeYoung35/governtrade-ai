// Everything that happened: headline counts, the daily outcomes chart, every order with search and filters, and the
// workspace's events. Merges the old Governance and Monitoring pages. Pause all agents is here as an emergency stop.
// The workspace state carries only the newest orders and events, so this screen reads its data from the server, which
// filters, counts and pages every order (order_views.py), and reads again after each workspace refresh.
import {useEffect, useState} from 'preact/hooks';
import {html} from '../html.js';
import {Icon} from '../ui/icons.js';
import {action} from '../api.js';
import {setStore, useStore} from '../store.js';
import {when} from '../format.js';
import {STATUS_FILTERS, CATEGORIES, DECISIONS, viewerTimeZone} from '../activity.js';
import {pageUrls, useReads} from '../remote.js';
import {statusOf, eventKind} from '../governor-text.js';
import {DecisionsTable} from '../ui/DecisionsTable.js';
import {orderHref} from '../ui/ApprovalCard.js';

const PAGE = 50;
const EVENT_PAGE = 20;  // the state carries the newest 20 events; more are read from the server
const SEARCH_DELAY_MS = 250;
const NO_FILTERS = {query: '', status: '', agent: '', category: '', decision: ''};
const SERIES = [['filled', 'Filled'], ['review', 'Needed your approval'], ['blocked', 'Blocked or denied']];
const dayLabel = day => new Date(`${day}T12:00:00`).toLocaleDateString('en-US', {month: 'short', day: 'numeric'});  // a local day
const capitalised = text => text.charAt(0).toUpperCase() + text.slice(1);

// The value, once it has stopped changing for `ms`: one search read per pause in typing, not per key.
function useSettled(value, ms) {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value]);
  return settled;
}

function Counts({counts}) {
  const c = counts || {};
  const items = [['Requested', c.requested, 'Every proposal the governor saw'], ['Ran automatically', c.automatic, 'Filled within your limits'],
    ['Waiting for you', c.awaiting, 'Approve or deny under Approvals'], ['Blocked or denied', c.blocked, 'Stopped by a rule or by you']];
  return html`<section class="card" aria-busy=${!counts}><dl class="money-strip">
    ${items.map(([label, n, note]) => html`<div><dt>${label}</dt><dd class="num">${n ?? '–'}</dd><small>${note}</small></div>`)}
  </dl></section>`;
}

// Grouped bars, one group per day. Status colours cannot be told apart by colour alone (red/amber under deuteranopia),
// so each series also has a fixed position, a legend, hatching for "blocked" and a table view.
// While the counts load, the chart keeps its shape (legend, plot area, table toggle) so the page does not jump.
function OutcomesChart({days}) {
  const max = Math.max(1, ...(days || []).flatMap(d => SERIES.map(([key]) => d[key])));
  return html`<section class="card" aria-busy=${!days}>
    <div class="card-head"><h2>Daily outcomes</h2><span class="muted chart-scope">${days?.length ? `Last ${days.length} day${days.length === 1 ? '' : 's'} with proposals` : ''}</span></div>
    <div class="card-body">
      ${days && !days.length ? html`<p class="muted">The chart fills in as your agents propose trades.</p>` : html`
        <ul class="chart-legend">${SERIES.map(([key, label]) => html`<li><i class=${`swatch outcome-${key}`} aria-hidden="true"></i>${label}</li>`)}</ul>
        ${!days ? html`<div class="outcome-chart outcome-chart-loading"><p class="muted">Loading the chart…</p></div>` : html`
        <div class="outcome-chart" role="img" aria-label="Daily outcomes. The table under the chart lists the numbers.">
          ${days.map(d => html`<div class="outcome-day">
            <div class="outcome-bars">${SERIES.map(([key, label]) => html`<span class=${`outcome-bar outcome-${key}`} style=${`--h:${d[key] / max * 100}%`}
              data-tip=${`${dayLabel(d.day)}: ${d[key]} ${label.toLowerCase()}`}></span>`)}</div>
            <span class="outcome-label">${dayLabel(d.day)}</span>
          </div>`)}
        </div>`}
        <details class="chart-table"><summary>Show as a table</summary>
          <table class="decisions-table"><thead><tr><th scope="col">Day</th>${SERIES.map(([, label]) => html`<th scope="col">${label}</th>`)}</tr></thead>
            <tbody>${(days || []).map(d => html`<tr><td>${dayLabel(d.day)}</td>${SERIES.map(([key]) => html`<td class="num">${d[key]}</td>`)}</tr>`)}</tbody></table>
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
      <span class="muted">${matching == null ? 'Loading orders…' : active ? `${matching} of ${total} orders match` : `${total} order${total === 1 ? '' : 's'}`}</span>
      ${active ? html`<button class="btn btn-ghost btn-sm" onClick=${clear}>Clear filters</button>` : null}
    </div>
  </section>`;
}

function Events({events, total, more, loading}) {
  return html`<section class="card">
    <div class="card-head"><h2>Events</h2></div>
    ${!events.length ? html`<div class="empty"><p>No events yet. Connections, rule changes and order results appear here.</p></div>` : html`
      <ol class="event-list">${events.map(e => html`<li class="event-row" key=${e.id}>
        <div class="event-main"><p>${e.text}</p><span class="muted">${when(e.time)}</span></div>
        <div class="event-side"><span class="pill pill-neutral">${eventKind(e.kind)}</span>${e.trade ? html`<a href=${orderHref(e.trade)}>Order</a>` : null}</div>
      </li>`)}</ol>
      ${total > events.length ? html`<div class="card-foot"><button class="btn btn-sm" disabled=${loading} onClick=${more}>${loading ? 'Loading…' : `Show ${Math.min(EVENT_PAGE, total - events.length)} more`}</button></div>` : null}`}
  </section>`;
}

export function Activity({workspace}) {
  const {busy} = useStore();
  const [filters, setFilters] = useState(NO_FILTERS);
  const [wanted, setWanted] = useState(PAGE);
  const [eventsWanted, setEventsWanted] = useState(EVENT_PAGE);
  const query = useSettled(filters.query.trim(), SEARCH_DELAY_MS);
  const summary = useReads([`/api/activity?tz=${encodeURIComponent(viewerTimeZone())}`], workspace);
  const pages = useReads(pageUrls('/api/orders', {...filters, query}, wanted), workspace);
  const older = useReads(eventsWanted > EVENT_PAGE ? pageUrls('/api/events', {}, eventsWanted) : null, workspace);
  const orders = pages.data ? pages.data.flatMap(p => p.orders) : null;
  const matching = pages.data?.[0].matching, total = pages.data?.[0].total ?? workspace.orderStats?.total ?? 0;
  const events = eventsWanted > EVENT_PAGE && older.data ? older.data.flatMap(p => p.events) : workspace.events || [];
  // The state counts only the events it loaded (a Postgres workspace loads the newest 500); a page read counts all.
  const eventTotal = (eventsWanted > EVENT_PAGE ? older.data?.[0].total : null) ?? workspace.eventCount ?? events.length;
  const error = summary.error || pages.error || older.error;
  const running = workspace.permissions?.automation !== false;
  const update = (key, value) => { setFilters(f => ({...f, [key]: value})); setWanted(PAGE); };

  async function pauseAll() {
    try {
      await action('permissions', {values: {automation: false}});
      setStore({toast: 'All agents paused. Turn trading back on under Rules.'});
    } catch (e) {
      setStore({error: e.message, errorSource: 'action'});
    }
  }

  return html`<div class="section-stack">
    <div class="page-head">
      <p>Every order your agents proposed, what the governor decided, and what happened in your workspace.</p>
      ${running ? html`<button class="btn btn-danger-outline" disabled=${busy} onClick=${pauseAll}>Pause all agents</button>`
        : html`<a class="btn" href="/rules"><${Icon} name="rules" />Trading is paused · Rules</a>`}
    </div>
    ${error ? html`<p class="notice notice-error" role="alert">${error.message}</p>` : null}
    <${Counts} counts=${summary.data?.[0].counts} />
    <${OutcomesChart} days=${summary.data?.[0].daily} />
    <${Filters} filters=${filters} update=${update} clear=${() => { setFilters(NO_FILTERS); setWanted(PAGE); }} workspace=${workspace} matching=${matching} total=${total} />
    <${DecisionsTable} workspace=${workspace} orders=${orders || []} limit=${wanted} title="Orders" activity
      empty=${!orders ? 'Loading orders…' : total ? 'No orders match these filters.' : "No orders yet. Your agents' proposals will appear here."} />
    ${orders && matching > orders.length ? html`<button class="btn activity-more" disabled=${pages.loading} onClick=${() => setWanted(orders.length + PAGE)}>
      ${pages.loading ? 'Loading…' : `Show ${Math.min(PAGE, matching - orders.length)} more orders`}</button>` : null}
    <${Events} events=${events} total=${eventTotal} loading=${older.loading} more=${() => setEventsWanted(events.length + EVENT_PAGE)} />
  </div>`;
}
