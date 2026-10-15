import {html} from '../html.js';
import {Creature} from '../creature/Creature.js';
import {lookFor} from '../creature/generate.js';
import {useAgentMood} from '../creature/useAgentMood.js';
function Agent({agent, workspace}) {
  const {mood, persistent, replay} = useAgentMood(agent, workspace);
  return html`<a class="lineup-agent" href=${`/agents/${encodeURIComponent(agent.id)}`}>
    <${Creature} look=${lookFor(agent)} size=${72} mood=${mood} replay=${replay} live=${workspace.accounts?.find(a => a.id === agent.accountId)?.environment === 'live'} />
    <strong>${agent.name}</strong><span class="muted">${persistent}</span>
  </a>`;
}
export function AgentLineup({workspace}) {
  return html`<section class="card"><div class="card-head"><h2>Your agents</h2><a href="/agents/new">New agent</a></div><div class="agent-lineup">
    ${(workspace.agentConfigs || []).filter(a => a.status !== 'deleted').map(agent => html`<${Agent} key=${agent.id} agent=${agent} workspace=${workspace} />`)}
  </div></section>`;
}
