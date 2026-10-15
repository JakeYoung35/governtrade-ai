import {html} from '../html.js';
import {Icon} from '../ui/icons.js';
import {MoneyStrip} from '../ui/MoneyStrip.js';
import {Approvals} from './Approvals.js';
import {AgentLineup} from '../ui/AgentLineup.js';
import {DecisionsTable} from '../ui/DecisionsTable.js';

function Step({done, title, detail, href, cta}) {
  return html`<li class=${'step' + (done ? ' done' : '')}>
    <span class="step-mark" aria-hidden="true">${done ? html`<${Icon} name="check" />` : null}</span>
    <div class="step-text"><strong>${title}</strong><span>${detail}</span></div>
    ${done ? html`<span class="visually-hidden">Done</span>` : html`<a class="btn btn-sm" href=${href}>${cta}</a>`}
  </li>`;
}

export function Home({workspace}) {
  const accounts = (workspace.accounts || []).filter(a => a.status !== 'disconnected');
  const agents = (workspace.agentConfigs || []).filter(a => a.status !== 'deleted');
  const ready = accounts.length && agents.length;
  if (ready) {
    return html`<div class="section-stack"><${MoneyStrip} workspace=${workspace} /><${Approvals} workspace=${workspace} home /><${AgentLineup} workspace=${workspace} /><${DecisionsTable} workspace=${workspace} /></div>`;
  }
  return html`<section class="card get-started" aria-labelledby="get-started">
    <div class="card-head"><h2 id="get-started">Get started</h2></div>
    <ol class="steps">
      <${Step} done=${true} title="Set your trading rules" detail="Every trade has to pass them." />
      <${Step} done=${accounts.length > 0} title="Connect Kalshi" detail="Demo or Live. Your money stays in your Kalshi account." href="/accounts" cta="Connect" />
      <${Step} done=${agents.length > 0} title="Create your first agent" detail="It proposes trades; the governor decides." href="/agents/new" cta="Create agent" />
    </ol>
  </section>`;
}
