// Errors a person can act on from the server helpers, and how often the workspace is polled.
import assert from 'node:assert/strict';
import {getStore, resetStore, setStore, startPolling} from '../../static/js/store.js';
import {action, pollWorkspace, readJson} from '../../static/js/api.js';

globalThis.document = {visibilityState: 'visible', addEventListener() {}, removeEventListener() {}};
globalThis.location = {hostname: 'localhost', search: ''};
const state = {orders: [], accounts: []};
const ready = () => resetStore({phase: 'ready', workspace: state, auth: {mode: 'local'}});

// A proxy's HTML error page (the server restarting) reads as "unavailable", not as a JSON parse error.
const htmlPage = status => ({ok: status < 400, status, json: async () => { throw SyntaxError("Unexpected token '<'"); }});
await assert.rejects(readJson(htmlPage(503)), /unavailable right now \(HTTP 503\)/);
await assert.rejects(readJson({ok: true, status: 200, json: htmlPage(200).json}), /unreadable response/);
ready();
globalThis.fetch = async () => htmlPage(503);
await assert.rejects(action('refresh'), /unavailable right now \(HTTP 503\)/);
assert.equal(getStore().busy, false);

// Polling only reads the workspace; it never sends an action that would refresh the venue.
const requested = [];
globalThis.fetch = async (url, options) => { requested.push([url, options?.method || 'GET']); return {ok: true, status: 200, json: async () => ({state, csrf: 't'})}; };
ready();
await pollWorkspace();
assert.deepEqual(requested, [['/api/state?main', 'GET']]);

// The poll slows down when nothing is in flight and speeds up while an order is waiting.
const delays = [];
const realSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms) => { delays.push(ms); return 1; };
globalThis.clearTimeout = () => {};
ready();
const stop = startPolling(async () => {});
assert.equal(delays.at(-1), 15000);
setStore({workspace: {...state, orders: [], pendingOrders: [], orderStats: {inFlight: 1}, recentOrders: []}});
stop();
const fast = startPolling(async () => {});
assert.equal(delays.at(-1), 3000);
// A hidden tab does not schedule polls at all.
const count = delays.length;
globalThis.document = {visibilityState: 'hidden', addEventListener() {}, removeEventListener() {}};
fast();
startPolling(async () => {});
assert.equal(delays.length, count);
globalThis.setTimeout = realSetTimeout;

console.log('API errors and polling: unavailable server, unreadable response, poll cadence and hidden tab pass.');
