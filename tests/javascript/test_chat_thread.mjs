import assert from 'node:assert/strict';
import {thread, wasDelivered, governorLine, tradingBlocked} from '../../static/js/chat.js';
import {reasonText, statusOf, eventKind} from '../../static/js/governor-text.js';
import {money} from '../../static/js/format.js';

const you = text => ({role: 'customer', text});
const agent = text => ({role: 'agent', text});
const where = items => items.map(item => item.proposals.map(p => p.id));

// A proposal sits under the agent's reply to the message that asked for it.
assert.deepEqual(where(thread([you('buy'), agent('proposed')], [{id: 'p', messageIndex: 0}])), [[], ['p']]);
// With no stored reply it stays under the customer message, even when a later message follows.
assert.deepEqual(where(thread([you('buy'), you('again'), agent('ok')], [{id: 'p', messageIndex: 0}])), [['p'], [], []]);
assert.deepEqual(where(thread([you('buy')], [{id: 'p', messageIndex: 0}])), [['p']]);
// Several proposals keep their order; an index outside the conversation is ignored.
assert.deepEqual(where(thread([you('two'), agent('done')], [{id: 'a', messageIndex: 0}, {id: 'b', messageIndex: 0}, {id: 'x', messageIndex: 9}])), [[], ['a', 'b']]);
assert.deepEqual(thread(), []);

assert.equal(wasDelivered([you('hi'), agent('hello'), you('Buy YES ')], 2, 'Buy YES'), true);
assert.equal(wasDelivered([you('hi'), agent('hello')], 2, 'Buy YES'), false);
assert.equal(wasDelivered(undefined, 0, 'Buy YES'), false);
// The same text sent earlier does not count: a refused resend must keep its draft.
assert.equal(wasDelivered([you('Buy YES'), agent('done')], 2, 'Buy YES'), false);

const words = {money, reasonText};
assert.equal(governorLine({}, words), null);
assert.equal(governorLine({decision: {decision: 'rejected', reasonCodes: ['positionAlreadyOpen']}}, words), 'Governor rejected it · Position already open');
assert.equal(governorLine({decision: {decision: 'resized', approvedBudgetUsd: 12, contracts: 1, limitPrice: 0.55, reasonCodes: ['agentExposureCap', 'newCode']}}, words),
  'Governor resized it to $12.00 · 1 contract at up to $0.55 · Agent exposure cap, New code');
assert.equal(governorLine({decision: {decision: 'approved', approvedBudgetUsd: 40, contracts: 72, limitPrice: 0.55, reasonCodes: []}}, words),
  'Governor approved $40.00 · 72 contracts at up to $0.55');

// The same order of reasons as AgentChat._trading_blocked.
const ready = {rules: {values: {}}, permissions: {automation: true}};
const scout = {name: 'Scout', mandate: {}, status: 'active'};
const connected = {status: 'connected'};
assert.equal(tradingBlocked(ready, scout, connected), null);
assert.equal(tradingBlocked({...ready, rules: {values: null}}, scout, connected), 'Set your trading rules first.');
assert.equal(tradingBlocked({...ready, permissions: {automation: false}}, {...scout, status: 'paused'}, connected), 'All-agent trading is switched off in Rules.');
assert.equal(tradingBlocked({...ready, permissions: {}}, scout, connected), 'All-agent trading is switched off in Rules.');
assert.equal(tradingBlocked({permissions: {automation: true}}, scout, connected), 'Set your trading rules first.');
assert.equal(tradingBlocked(ready, {...scout, mandate: null, status: 'draft'}, connected), "Set Scout's mandate first.");
assert.equal(tradingBlocked(ready, {...scout, status: 'paused'}, connected), 'Scout is paused. Resume it to trade.');
assert.match(tradingBlocked(ready, scout, {status: 'error'}), /not connected/);
assert.match(tradingBlocked(ready, scout, undefined), /not connected/);

assert.deepEqual(statusOf('awaiting approval'), {label: 'Waiting for you', tone: 'review'});
assert.equal(eventKind('REQUIRE_APPROVAL'), 'Needs approval');
assert.equal(eventKind('RULES'), 'Rules');
assert.equal(eventKind('SOME_NEW_KIND'), 'Some new kind');
assert.equal(eventKind(undefined), 'Update');
assert.deepEqual(statusOf('filled'), {label: 'Filled', tone: 'allow'});
assert.deepEqual(statusOf('somethingNew'), {label: 'SomethingNew', tone: 'neutral'});
assert.deepEqual(statusOf(undefined), {label: 'Unknown', tone: 'neutral'});
console.log('Chat thread tests passed');
