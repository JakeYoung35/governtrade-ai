import assert from 'node:assert/strict';
import {filterOrders, activityCounts, dailyOutcomes as byLocalDay, localDay} from '../../static/js/activity.js';
// Group by UTC here so the expectations do not depend on the machine's time zone.
const utcDay = time => (/^\d{4}-\d{2}-\d{2}T/.test(time || '') ? time.slice(0, 10) : null);
const dailyOutcomes = (orders, days) => byLocalDay(orders, days, utcDay);
assert.equal(localDay('not a time'), null);
assert.equal(localDay(undefined), null);
assert.match(localDay('2026-10-14T12:00:00Z'), /^2026-10-1[345]$/);

const order = (id, extra = {}) => ({id, agent: 'a', status: 'filled', decision: 'ALLOW', time: '2026-10-14T10:00:00Z',
  market: {id: 'KXBTC-1', name: 'Bitcoin above $100k', agent: 'crypto'}, request: 'Momentum.', direction: 'Yes', evaluations: [], ...extra});
const orders = [
  order('o1', {filled: true, automatic: true}),
  order('o2', {agent: 'b', status: 'awaiting approval', decision: 'REQUIRE_APPROVAL', market: {id: 'KXNFL-1', name: 'Eagles win', agent: 'sports'},
    evaluations: [{outcome: 'REQUIRE_APPROVAL'}], time: '2026-10-13T09:00:00Z'}),
  order('o3', {status: 'blocked', decision: 'BLOCK', time: '2026-10-13T11:00:00Z'}),
  order('o4', {status: 'filled', filled: true, automatic: false, evaluations: [{outcome: 'REQUIRE_APPROVAL'}, {outcome: 'ALLOW'}]}),
  order('o5', {status: 'denied', decision: 'REQUIRE_APPROVAL', time: 'not a time'}),
];
const agents = new Map([['a', {name: 'Rally'}], ['b', {name: 'Scout'}]]);
const ids = list => list.map(o => o.id);

assert.deepEqual(ids(filterOrders(orders, {}, agents)), ['o1', 'o2', 'o3', 'o4', 'o5']);
assert.deepEqual(ids(filterOrders(orders, {status: 'blocked'}, agents)), ['o3']);
assert.deepEqual(ids(filterOrders(orders, {agent: 'b'}, agents)), ['o2']);
assert.deepEqual(ids(filterOrders(orders, {category: 'sports'}, agents)), ['o2']);
assert.deepEqual(ids(filterOrders(orders, {decision: 'BLOCK'}, agents)), ['o3']);
// Search matches the market, the agent's name, the order ID and the reasoning, word by word, in any case.
assert.deepEqual(ids(filterOrders(orders, {query: 'eagles'}, agents)), ['o2']);
assert.deepEqual(ids(filterOrders(orders, {query: 'scout'}, agents)), ['o2']);
assert.deepEqual(ids(filterOrders(orders, {query: 'O4'}, agents)), ['o4']);
assert.deepEqual(ids(filterOrders(orders, {query: 'rally bitcoin'}, agents)), ['o1', 'o3', 'o4', 'o5']);
assert.deepEqual(ids(filterOrders(orders, {query: 'status'}, agents)), []);  // field names are not searched
assert.deepEqual(ids(filterOrders(orders, {query: 'denied'}, agents)), ['o5']);  // statuses are
assert.deepEqual(ids(filterOrders(orders, {query: 'eagles', agent: 'a'}, agents)), []);
assert.deepEqual(filterOrders(), []);

assert.deepEqual(activityCounts(orders), {requested: 5, automatic: 1, awaiting: 1, blocked: 2});
assert.deepEqual(activityCounts(), {requested: 0, automatic: 0, awaiting: 0, blocked: 0});

// Days in order; an approved-then-filled order counts in both; bad timestamps are skipped.
assert.deepEqual(dailyOutcomes(orders), [
  {day: '2026-10-13', filled: 0, review: 1, blocked: 1},
  {day: '2026-10-14', filled: 2, review: 1, blocked: 0},
]);
const many = Array.from({length: 10}, (_, i) => order(`d${i}`, {time: `2026-10-${String(i + 1).padStart(2, '0')}T00:00:00Z`}));
assert.deepEqual(dailyOutcomes(many).map(d => d.day), ['2026-10-04', '2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08', '2026-10-09', '2026-10-10']);
assert.deepEqual(dailyOutcomes([]), []);
console.log('Activity tests passed: filters, search, counts, daily outcomes');
