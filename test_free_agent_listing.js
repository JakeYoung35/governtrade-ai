// Run with node test_free_agent_listing.js. No browser, venue or model credentials required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let selected = null;
const context = vm.createContext({assert, console, setTimeout, clearTimeout, clearInterval,
  document: {addEventListener() {}, querySelector(s) { return s === '#free-agent-account' && selected ? {value: selected} : null; }},
  setSelected(v) { selected = v; }});
const source = fs.readFileSync('static/app.js', 'utf8').replace('});initializeAuth();', '});');
vm.runInContext(source, context);
vm.runInContext(`
(async () => {
  render = () => {};
  const free = {label: 'GovernTrade free agent (Gemini, no API key needed)', shortLabel: 'GovernTrade free agent',
                models: ['gemini-3.1-flash-lite'], free: true, dailyMessages: 20, notice: 'Uses the shared Gemini free tier.'};
  const demo = {id: 'acct-demo', name: 'Demo', environment: 'sandbox', status: 'connected'};
  const live = {id: 'acct-live', name: 'Live', environment: 'live', status: 'connected'};

  // Without a server key the listing is not shown.
  state = {providers: {openai: {label: 'OpenAI', models: ['gpt-5']}}, agentConfigs: [], accounts: [demo]};
  assert.equal(freeAgentListing(), '');
  assert.ok(!agentCards().includes('free-listing'));

  // Offered, under the agent grid, with one click to set up.
  state = {providers: {'governtrade-free': free}, agentConfigs: [], accounts: [demo]};
  let html = agentCards();
  assert.ok(html.indexOf('free-listing') > html.indexOf('agent-grid'), 'listing sits under the agents');
  assert.ok(html.includes('GovernTrade free agent') && html.includes('gemini-3.1-flash-lite') && html.includes('20 free messages'));
  assert.ok(html.includes('data-use-free-agent') && !html.includes('free-agent-account'));

  // One click saves the whole LLM setup and opens mandate setup.
  let sent = null;
  api = async (action, values) => { sent = {action, values}; return {agentId: 'new-free-agent'}; };
  error = '';
  await useFreeAgent();
  assert.deepEqual(sent, {action: 'agent-save', values: {name: 'Free Agent', provider: 'governtrade-free',
                                                         model: 'gemini-3.1-flash-lite', accountId: 'acct-demo'}});
  assert.equal(agent, 'new-free-agent');
  assert.equal(page, 'Selected Trading Agent');
  assert.equal(modal.type, 'mandate');
  assert.equal(modal.id, 'new-free-agent');

  // Several accounts: the user picks one.
  state = {providers: {'governtrade-free': free}, agentConfigs: [], accounts: [demo, live]};
  assert.ok(agentCards().includes('free-agent-account'));
  setSelected('acct-live');
  await useFreeAgent();
  assert.equal(sent.values.accountId, 'acct-live');
  setSelected(null);

  // A failed save stays on the page.
  modal = null; api = async () => { error = 'The free agent could not be reached right now.'; return undefined; };
  await useFreeAgent();
  assert.equal(modal, null);

  // No connected account: connect first. Already using it: open it.
  state = {providers: {'governtrade-free': free}, agentConfigs: [], accounts: []};
  assert.ok(agentCards().includes('Connect Kalshi first'));
  state = {providers: {'governtrade-free': free}, accounts: [demo],
           agentConfigs: [{id: 'f1', name: 'Free Agent', provider: 'governtrade-free', model: 'gemini-3.1-flash-lite', status: 'active', accountId: 'acct-demo'}]};
  html = agentCards();
  assert.ok(html.includes('Open free agent') && html.includes('data-agent="f1"') && !html.includes('data-use-free-agent'));
  console.log('Free agent listing: hidden without a key, under the agents, one-click setup to mandate, account choice, connect and open states pass.');
})()
`, context).catch(error => {console.error(error);process.exitCode = 1});
