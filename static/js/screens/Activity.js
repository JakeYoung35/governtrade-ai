import {useState} from 'preact/hooks';
import {html} from '../html.js';
import {action} from '../api.js';
import {setStore,useStore} from '../store.js';
import {when} from '../format.js';
import {filterOrders,dailyOutcomes} from '../activity.js';
import {DecisionsTable} from '../ui/DecisionsTable.js';
export function Activity({workspace}) {
  const [filters,set] = useState({query:'',status:'',agent:'',category:'',decision:''}); const {busy} = useStore(); const [limit,more]=useState(50);
  const orders=workspace.orders || [], filtered=filterOrders(orders,filters), days=dailyOutcomes(orders);
  const maximum=Math.max(1,...days.map(([,values])=>Object.values(values).reduce((a,b)=>a+b,0)));
  const update=(key,value)=>{set({...filters,[key]:value});more(50);};
  async function pause() {try{await action('permissions',{values:{automation:false}});setStore({toast:'All agents paused.'});}catch(e){setStore({error:e.message});}}
  const options=(label,key,items)=>html`<label class="field">${label}<select class="input" value=${filters[key]} onChange=${e=>update(key,e.target.value)}><option value="">All</option>${items.map(([value,text])=>html`<option value=${value}>${text}</option>`)}</select></label>`;
  return html`<div class="section-stack"><div class="page-head"><p>Orders, governance decisions and workspace events.</p><button class="btn" disabled=${busy || !workspace.permissions.automation} onClick=${pause}>Pause all agents</button></div>
    <section class="card card-body"><dl class="money-strip">${[['Requests',orders.length],['Allowed',orders.filter(o=>o.decision==='ALLOW').length],['Needs approval',orders.filter(o=>o.status==='awaiting approval').length],['Blocked',orders.filter(o=>o.decision==='BLOCK').length]].map(([label,n])=>html`<div><dt>${label}</dt><dd class="num">${n}</dd></div>`)}</dl></section>
    <section class="card card-body section-stack"><h2>Daily governance outcomes</h2><p class="muted">Latest recorded verdict per order, grouped by proposal day.</p>${days.length?days.map(([day,values])=>html`<div class="outcome-row"><span>${day}</span><div class="outcome-bar" role="img" aria-label=${Object.entries(values).map(([k,v])=>`${k}: ${v}`).join(', ')}>${Object.entries(values).map(([key,n])=>html`<span class=${'outcome-'+key} style=${`width:${n/maximum*100}%`} title=${`${key}: ${n}`}></span>`)}</div><span class="num">${Object.values(values).reduce((a,b)=>a+b,0)}</span></div>`):html`<p>No decisions yet.</p>`}<p class="muted">Green: allowed · Amber: approval required · Red: blocked</p></section>
    <section class="card card-body"><div class="activity-filters"><label class="field">Search orders<input class="input" value=${filters.query} onInput=${e=>update('query',e.target.value)} /></label>
    ${options('Status','status',[...new Set(orders.map(o=>o.status))].map(x=>[x,x]))}${options('Agent','agent',(workspace.agentConfigs || []).map(a=>[a.id,a.name]))}${options('Category','category',[...new Set(orders.map(o=>o.market?.agent).filter(Boolean))].map(x=>[x,x]))}${options('Decision','decision',[['ALLOW','Allowed'],['REQUIRE_APPROVAL','Needs approval'],['BLOCK','Blocked']])}</div><p>${filtered.length} matching orders</p></section>
    <${DecisionsTable} workspace=${{...workspace,orders:filtered.slice(0,limit)}} limit=${limit} title="Orders" activity />${filtered.length>limit?html`<button class="btn" onClick=${()=>more(limit+50)}>Load more orders</button>`:null}
    <section class="card card-body section-stack"><h2>Notifications and events</h2>${(workspace.events || []).slice(0,100).map(e=>html`<article class="audit-row"><div><strong>${e.text}</strong><p class="muted">${when(e.time)} · ${e.kind}</p></div>${e.trade?html`<a href=${`/activity?order=${encodeURIComponent(e.trade)}`}>Order details</a>`:null}</article>`)}${!workspace.events?.length?html`<p>No events yet.</p>`:null}</section>
  </div>`;
}
