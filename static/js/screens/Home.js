// Home: setup steps until the workspace can trade, then money, approvals, agents and recent decisions. A workspace
// with history keeps its dashboard under the steps when something needs fixing (an account error, a missing mandate).
import {html} from '../html.js';
import {Icon} from '../ui/icons.js';
import {MoneyStrip} from '../ui/MoneyStrip.js';
import {Approvals} from './Approvals.js';
import {AgentLineup} from '../ui/AgentLineup.js';
import {DecisionsTable} from '../ui/DecisionsTable.js';
import {readiness} from '../readiness.js';

function Step({done, title, detail, href, cta}) {
  return html`<li class=${'step' + (done ? ' done' : '')}>
    <span class="step-mark" aria-hidden="true">${done ? html`<${Icon} name="check" />` : null}</span>
    <div class="step-text"><strong>${title}</strong><span>${detail}</span></div>
    ${done ? html`<span class="visually-hidden">Done</span>` : html`<a class="btn btn-sm" href=${href}>${cta}</a>`}
  </li>`;
}

function Setup({workspace, state}) {
  const account = state.troubled && !state.connected
    ? {detail: `${state.troubled.name} needs attention. Test it, or add new keys, under Accounts.`, cta: 'Fix connection'}
    : {detail: 'Demo or Live, then test it. Your money stays in your Kalshi account.', cta: 'Connect'};
  const agent = state.waiting
    ? {detail: `${state.waiting.name} is set up and trades once its Kalshi account is connected.`, href: '/accounts', cta: 'Accounts'}
    : state.needsMandate
      ? {detail: `${state.needsMandate.name} needs its mandate before it can trade.`, href: `/agents/new?agent=${state.needsMandate.id}`, cta: 'Set mandate'}
      : {detail: 'It proposes trades; the governor decides. Give it a mandate to start.', href: '/agents/new', cta: 'Create agent'};
  return html`<section class="card get-started" aria-labelledby="get-started">
    <div class="card-head"><h2 id="get-started">${(workspace.orders || []).length ? 'Finish setting up to trade' : 'Get started'}</h2></div>
    <ol class="steps">
      <${Step} done=${true} title="Set your trading rules" detail="Every trade has to pass them." />
      <${Step} done=${state.connected} title="Connect Kalshi" detail=${account.detail} href="/accounts" cta=${account.cta} />
      <${Step} done=${state.trading} title="Create an agent with a mandate" detail=${agent.detail} href=${agent.href} cta=${agent.cta} />
    </ol>
  </section>`;
}

export function Home({workspace}) {
  const state = readiness(workspace);
  const history = (workspace.orders || []).length > 0 || (workspace.agentConfigs || []).some(a => a.status !== 'deleted' && a.mandate);
  const dashboard = html`<${MoneyStrip} workspace=${workspace} /><${Approvals} workspace=${workspace} home /><${AgentLineup} workspace=${workspace} /><${DecisionsTable} workspace=${workspace} />`;
  if (state.ready) return html`<div class="section-stack">${dashboard}</div>`;
  return html`<div class="section-stack"><${Setup} workspace=${workspace} state=${state} />${history ? dashboard : null}</div>`;
}
