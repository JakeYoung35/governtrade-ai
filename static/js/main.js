// Entry point: mounts the app and decides which full-page state or screen to show.
import {render} from 'preact';
import {useEffect} from 'preact/hooks';
import {html} from './html.js';
import {useStore, setStore, startPolling} from './store.js';
import {useRoute} from './router.js';
import {initializeAuth, currentGeneration, pollWorkspace} from './api.js';
import {Icon} from './ui/icons.js';
import {Shell} from './ui/Shell.js';
import {Loading, Unavailable, LocalEntry, SignedOut} from './screens/SignIn.js';
import {RulesSetup} from './screens/RulesSetup.js';
import {Rules} from './screens/Rules.js';
import {Accounts} from './screens/Accounts.js';
import {Activity} from './screens/Activity.js';
import {AgentDetail} from './screens/AgentDetail.js';
import {Approvals} from './screens/Approvals.js';
import {Drawer} from './ui/Drawer.js';
import {Toast} from './ui/Toast.js';
import {Home} from './screens/Home.js';
import {Agents} from './screens/Agents.js';
import {NewAgent} from './screens/NewAgent.js';
import {Placeholder, NotFound} from './screens/Placeholder.js';

const TITLES = {
  home: 'Home', approvals: 'Approvals', agents: 'Agents', 'new-agent': 'New agent', agent: 'Agent',
  activity: 'Activity', rules: 'Rules', accounts: 'Accounts', demo: 'Demo', 'not-found': 'Not found',
};

function Screen({route, workspace}) {
  if (route.name === 'rules') return html`<${Rules} workspace=${workspace} />`;
  if (route.name === 'accounts') return html`<${Accounts} workspace=${workspace} />`;
  if (route.name === 'activity') return html`<${Activity} workspace=${workspace} />`;
  if (route.name === 'agent') return html`<${AgentDetail} key=${route.params.id} id=${route.params.id} workspace=${workspace} />`;
  if (route.name === 'approvals') return html`<${Approvals} workspace=${workspace} />`;
  if (route.name === 'home') return html`<${Home} workspace=${workspace} />`;
  if (route.name === 'agents') return html`<${Agents} workspace=${workspace} />`;
  if (route.name === 'new-agent') return html`<${NewAgent} key=${route.query.get('agent') || 'new'} workspace=${workspace} query=${route.query} />`;
  if (route.name === 'not-found') return html`<${NotFound} />`;
  return html`<${Placeholder} />`;
}

function ErrorBanner({error}) {
  if (!error) return null;
  return html`<div class="notice notice-error banner" role="alert">
    <${Icon} name="alert" /><span>${error}</span>
    <button class="btn btn-ghost btn-sm" onClick=${() => setStore({error: ''})}>Dismiss</button>
  </div>`;
}

function App() {
  const store = useStore();
  const route = useRoute();
  const {phase, workspace, auth, error} = store;
  const agentName = route.name === 'agent' ? workspace?.agentConfigs?.find(a => a.id === route.params.id && a.status !== 'deleted')?.name : null;
  const title = agentName || TITLES[route.name];
  useEffect(() => startPolling(pollWorkspace), []);
  useEffect(() => { if (!store.toast) return; const timer = setTimeout(() => setStore({toast: ''}), 5000); return () => clearTimeout(timer); }, [store.toast]);
  useEffect(() => {
    const setup = phase === 'ready' && workspace?.rules && !workspace.rules.values;
    document.title = setup ? 'Set your trading rules · GovernTrade' : phase === 'ready' && title ? `${title} · GovernTrade` : 'GovernTrade AI';
  }, [phase, title, workspace?.rules?.values]);

  if (phase === 'starting') return html`<${Loading} />`;
  if (phase === 'unavailable') return html`<${Unavailable} error=${error} />`;
  if (phase === 'local-entry') return html`<${LocalEntry} />`;
  if (phase === 'signed-out') return html`<${SignedOut} auth=${auth} error=${error} />`;
  if (workspace.rules && !workspace.rules.values) return html`<${RulesSetup} key=${currentGeneration()} workspace=${workspace} />`;
  return html`<${Shell} key=${currentGeneration()} route=${route} title=${title} workspace=${workspace} auth=${auth}>
    <${ErrorBanner} error=${error} />
    <${Screen} route=${route} workspace=${workspace} />
    <${Toast} message=${store.toast} onDismiss=${() => setStore({toast: ''})} />
    ${route.query.has('order') ? html`<${Drawer} id=${route.query.get('order')} workspace=${workspace} />` : null}
  </${Shell}>`;
}

render(html`<${App} />`, document.getElementById('app'));
initializeAuth();
