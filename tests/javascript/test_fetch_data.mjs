// fetchData: the reads behind Activity, Approvals and the order drawer. Errors carry their HTTP status, a 401 signs the
// user out like a poll does, and an answer for a user who has since signed out is dropped.
import assert from 'node:assert/strict';
import {getStore, resetStore} from '../../static/js/store.js';
import {fetchData, signOut} from '../../static/js/api.js';
globalThis.document = {visibilityState: 'visible'};
globalThis.location = {hostname: 'localhost'};
globalThis.sessionStorage = {removeItem() {}};
const response = (data, status = 200) => ({ok: status === 200, status, json: async () => data});
const ready = () => resetStore({phase: 'ready', workspace: {recentOrders: []}, auth: {mode: 'local'}});

ready();
const urls = [];
globalThis.fetch = async url => { urls.push(url); return response({order: {id: 'o1'}}); };
assert.deepEqual(await fetchData('/api/orders/o1'), {order: {id: 'o1'}});
assert.deepEqual(urls, ['/api/orders/o1']);

globalThis.fetch = async () => response({error: 'This order is not in your workspace.'}, 404);
await assert.rejects(fetchData('/api/orders/missing'), e => e.status === 404 && e.message === 'This order is not in your workspace.');
globalThis.fetch = async () => ({ok: false, status: 502, json: async () => { throw SyntaxError('HTML'); }});
await assert.rejects(fetchData('/api/orders'), /unavailable right now \(HTTP 502\)/);
globalThis.fetch = async () => { throw TypeError('Failed to fetch'); };
await assert.rejects(fetchData('/api/orders'), /Could not reach GovernTrade/);

// A 401 signs the user out and resolves undefined, so no screen shows an error for a session that ended.
globalThis.fetch = async url => url === '/api/auth' ? response({mode: 'google', user: null, csrf: 'new'}) : response({}, 401);
assert.equal(await fetchData('/api/activity?tz=UTC'), undefined);
assert.equal(getStore().phase, 'signed-out');
assert.equal(getStore().workspace, null);

// An answer that arrives after the user signed out belongs to the previous user: dropped.
ready();
let answer;
globalThis.fetch = () => new Promise(resolve => { answer = resolve; });
const stale = fetchData('/api/orders');
await signOut();
answer(response({orders: [{id: 'previous-user'}]}));
assert.equal(await stale, undefined);
console.log('Fetch data tests passed: data, 404 status, unreadable error, offline, 401, previous user');
