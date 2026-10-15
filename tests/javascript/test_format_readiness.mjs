import assert from 'node:assert/strict';
import {accountValue, workspaceValue} from '../../static/js/format.js';
import {readiness} from '../../static/js/readiness.js';

// accountValue follows the governor's equity: cash and positions required, missing reserved counts as 0.
assert.equal(accountValue({cash: 900, reserved: 50, positionValue: 50}), 1000);
assert.equal(accountValue({cash: '900.5', positionValue: '99.5'}), 1000);
assert.equal(accountValue({cash: 0, reserved: 0, positionValue: 0}), 0);  // a known empty account is $0
for (const bad of [null, undefined, {}, {cash: 'abc', positionValue: 1}, {cash: 1}, {cash: null, positionValue: 1},
                   {cash: 1, positionValue: NaN}, {cash: 1, positionValue: 1, reserved: 'x'}, {cash: true, positionValue: 1}, {cash: '', positionValue: 1}])
  assert.equal(accountValue(bad), null, JSON.stringify(bad));

// workspaceValue is never a partial total.
const connected = balance => ({status: 'connected', balance});
assert.equal(workspaceValue({accounts: [connected({cash: 100, positionValue: 0}), connected({cash: 50, positionValue: 50})]}), 200);
assert.equal(workspaceValue({accounts: [connected({cash: 100, positionValue: 0}), connected(null)]}), null);
assert.equal(workspaceValue({accounts: [connected({cash: 100, positionValue: 0}), {status: 'disconnected', balance: null}]}), 100);
assert.equal(workspaceValue({accounts: []}), null);

// Readiness matches the trading path: a connected account and an agent there with a validated model and a mandate.
const ready = {accounts: [{id: 'k', status: 'connected', name: 'Main'}],
  agentConfigs: [{id: 'a', status: 'active', accountId: 'k', modelValidated: true, mandate: {}, name: 'Rally'}]};
assert.equal(readiness(ready).ready, true);
assert.equal(readiness({...ready, accounts: [{...ready.accounts[0], status: 'error'}]}).ready, false);
assert.equal(readiness({...ready, accounts: [{...ready.accounts[0], status: 'error'}]}).troubled.name, 'Main');
assert.equal(readiness({...ready, accounts: [{...ready.accounts[0], status: 'error'}]}).waiting.name, 'Rally');  // set up, waiting on its account
assert.equal(readiness(ready).waiting, null);
assert.equal(readiness({...ready, accounts: [{...ready.accounts[0], status: 'disconnected', hasCredentials: true}]}).ready, false);
const draft = {...ready, agentConfigs: [{...ready.agentConfigs[0], mandate: null, status: 'draft'}]};
assert.equal(readiness(draft).ready, false);
assert.equal(readiness(draft).needsMandate.name, 'Rally');
assert.equal(readiness({...ready, agentConfigs: [{...ready.agentConfigs[0], modelValidated: false}]}).ready, false);
assert.equal(readiness({...ready, agentConfigs: [{...ready.agentConfigs[0], status: 'deleted'}]}).ready, false);
assert.equal(readiness({...ready, agentConfigs: [{...ready.agentConfigs[0], accountId: 'elsewhere'}]}).ready, false);
assert.equal(readiness({}).ready, false);
console.log('Format and readiness tests passed: account value, workspace value, readiness');
