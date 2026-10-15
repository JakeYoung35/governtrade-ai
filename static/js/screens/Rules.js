import {useState} from 'preact/hooks';
import {html} from '../html.js';
import {RulesForm} from '../ui/RulesForm.js';
import {action} from '../api.js';
import {useStore,setStore} from '../store.js';
import {money} from '../format.js';
export function Rules({workspace}) {
  const [error, fail] = useState(''); const {busy} = useStore();
  async function save(scope,values) {fail('');try{await action('permissions',{scope,values});setStore({toast:'Trading permissions saved.'});}catch(e){fail(e.message);}}
  return html`<div class="section-stack"><section class="card card-body"><label class="switch-row"><span><strong>Enable all-agent trading</strong><small>Switching off pauses every agent. Switching on does not resume paused agents.</small></span><input type="checkbox" role="switch" class="switch" checked=${workspace.permissions.automation} disabled=${busy} onChange=${e=>save('defaults',{automation:e.target.checked})} /></label></section>
    ${error ? html`<p class="notice notice-error" role="alert">${error}</p>` : null}
    <${RulesForm} workspace=${workspace} onSaved=${()=>setStore({toast:'Trading rules saved.'})} />
    <section class="card card-body section-stack"><h2>Account daily ceilings</h2><p>Each account can have a lower daily spending ceiling. Leave blank to use your workspace rule.</p>
      ${(workspace.accounts || []).map(a=>html`<${Ceiling} key=${a.id+':'+(a.ceiling?.dailyPercent ?? '')} account=${a} busy=${busy} save=${save} />`)}
    </section><section class="card card-body section-stack"><h2>Agent mandates</h2>${(workspace.agentConfigs || []).filter(a=>a.status!=='deleted').map(a=>html`<a href=${`/agents/${a.id}`}>${a.name} · ${a.mandate?'Edit mandate':'Set mandate'}</a>`)}</section></div>`;
}
function Ceiling({account, busy, save}) {
  const [value,set] = useState(account.ceiling?.dailyPercent ?? '');
  return html`<form class="field-row" onSubmit=${e=>{e.preventDefault();save(account.id,{dailyPercent:value===''?null:Number(value)});}}><label class="field">${account.name} · ${account.environment==='live'?'Live':'Demo'}<input class="input" type="number" min="0" max="100" step="any" value=${value} onInput=${e=>set(e.target.value)} /><span>Effective ceiling ${account.effectiveCeiling?.dailyPercent}% · ${money(account.effectiveCeiling?.dailyCap)}</span></label><button class="btn" disabled=${busy}>Save ceiling</button></form>`;
}
