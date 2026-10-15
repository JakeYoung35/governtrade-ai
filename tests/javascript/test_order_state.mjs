import assert from 'node:assert/strict';
import {countdown, hasInFlight, moodEvents, pendingOrders, windowOrders} from '../../static/js/order-state.js';
import {money} from '../../static/js/format.js';
const time = Date.parse('2026-10-14T16:00:00Z');
assert.deepEqual(countdown('2026-10-14T16:01:01Z', time), {expired: false, label: '1:01 remaining'});
assert.equal(countdown('2026-10-14T16:00:00Z', time).expired, true);
assert.equal(countdown('invalid', time).expired, true);
assert.equal(countdown(null, time).expired, false);
for (const value of [null, undefined, NaN, Infinity, 'no']) assert.equal(money(value), '–');
assert.equal(money(0), '$0.00');
// The server counts in-flight orders over every order; the state's window may not hold them.
assert.equal(hasInFlight({orderStats: {inFlight: 1}, recentOrders: []}), true);
assert.equal(hasInFlight({chatProposals: {a: [{status: 'queued'}]}}), true);
assert.equal(hasInFlight({orderStats: {inFlight: 0}, recentOrders: [{status: 'submission unknown'}]}), false);
assert.equal(hasInFlight({}), false);

// windowOrders: the newest and the waiting orders, once each.
assert.deepEqual(windowOrders({recentOrders: [{id: 'x'}, {id: 'y'}], pendingOrders: [{id: 'y'}, {id: 'z'}]}).map(o => o.id), ['x', 'y', 'z']);
assert.deepEqual(pendingOrders({}), []);

const o = {id: 'order', agent: 'a', accountId: 'demo', status: 'awaiting approval', time: '2026-10-14T16:00:00Z'};
const state = {recentOrders: [o], pendingOrders: [o], accounts: [{id: 'demo', environment: 'sandbox'}]};
assert.deepEqual(moodEvents(null, state), {});
assert.deepEqual(moodEvents({recentOrders: []}, state), {a: 'proposed'});
const trimmed = {...state, recentOrders: [{...o, governor: {approvedBudgetUsd: '5', maxBudgetUsd: '10'}}], pendingOrders: []};
assert.deepEqual(moodEvents(state, trimmed), {a: 'trimmed'});
assert.deepEqual(moodEvents(trimmed, trimmed), {});
const filled = {...state, recentOrders: [{...o, status: 'filled'}], pendingOrders: []};
assert.deepEqual(moodEvents(state, filled), {a: 'filled'});
assert.deepEqual(moodEvents(state, {...filled, accounts: [{id: 'demo', environment: 'live'}]}), {});
assert.deepEqual(moodEvents(filled, filled), {});

// An older order entering the window (a waiting order past the state's first page) is not a new proposal...
const older = {id: 'older', agent: 'b', accountId: 'demo', status: 'awaiting approval', time: '2026-10-01T00:00:00Z',
  governor: {decision: 'resized'}};
assert.deepEqual(moodEvents(state, {...state, pendingOrders: [o, older]}), {});
// ...but one newer than everything the previous window held is.
const newer = {...older, id: 'newer', time: '2026-10-14T17:00:00Z', governor: undefined};
assert.deepEqual(moodEvents(state, {...state, recentOrders: [newer, o]}), {b: 'proposed'});
// Each agent's newest event wins, whichever list the order came from.
const second = {...newer, id: 'second', agent: 'a', time: '2026-10-14T18:00:00Z', status: 'filled'};
assert.deepEqual(moodEvents(state, {...state, recentOrders: [second, o], pendingOrders: [{...o, governor: {decision: 'resized'}}]}), {a: 'proposed'});
console.log('Order state tests passed');
