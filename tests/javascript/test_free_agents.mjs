// The free agent listing's data rules. The listing component needs a DOM, so its helpers are tested here.
import assert from 'node:assert/strict';

// The screens import the creature, which reads the pointer and motion preferences when it loads.
globalThis.matchMedia = () => ({matches: false, addEventListener() {}, removeEventListener() {}});
globalThis.addEventListener = () => {};
globalThis.document = {addEventListener() {}, removeEventListener() {}, querySelectorAll: () => []};
const {connectedAccounts, accountLabel} = await import('../../static/js/screens/Agents.js');
const {freeMessagesLeft} = await import('../../static/js/screens/AgentDetail.js');

const providers = {openai: {label: 'OpenAI', models: ['gpt-5']}, 'governtrade-free': {label: 'GovernTrade free agent', free: true, models: ['gemini-3.1-flash-lite']}};
const free = {provider: 'governtrade-free'};

// The quota line shows only for the free provider, and only when the server reports a quota.
assert.equal(freeMessagesLeft({providers, freeAgentQuota: {left: 12, limit: 20}}, {provider: 'openai'}), null);
assert.equal(freeMessagesLeft({providers, freeAgentQuota: null}, free), null);
assert.deepEqual(freeMessagesLeft({providers, freeAgentQuota: {left: 12, limit: 20}}, free), {left: 12, total: 20});

// "Connect Kalshi first" depends on connected accounts only; a failed or pending account does not count.
const demo = {id: 'acct-demo', name: 'Demo', environment: 'sandbox', status: 'connected'};
const pending = {id: 'acct-new', name: 'New', environment: 'sandbox', status: 'pending'};
assert.deepEqual(connectedAccounts({accounts: []}), []);
assert.deepEqual(connectedAccounts({accounts: [demo, pending]}).map(a => a.id), ['acct-demo']);
assert.deepEqual(connectedAccounts({}), []);

// Account names in the picker and the "Trades on" line.
assert.equal(accountLabel(demo), 'Demo · Kalshi Demo');
assert.equal(accountLabel({name: 'Kalshi Live', environment: 'live'}), 'Kalshi Live');
assert.equal(accountLabel(undefined), 'No account');

console.log('Free agent helpers: quota visibility, connected accounts, account labels pass.');
