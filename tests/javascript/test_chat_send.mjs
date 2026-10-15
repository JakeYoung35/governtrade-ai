// Run: node --loader ./tests/javascript/module-loader.mjs tests/javascript/test_chat_send.mjs
import assert from 'node:assert/strict';
import {getStore, resetStore} from '../../static/js/store.js';
import {sendChat} from '../../static/js/api.js';
import {loadDraft, saveDraft, clearDraft, requestIdFor, draftOwner} from '../../static/js/chat-draft.js';

const memory = new Map();
globalThis.sessionStorage = {getItem: k => memory.get(k) ?? null, setItem: (k, v) => memory.set(k, String(v)), removeItem: k => memory.delete(k)};
globalThis.document = {visibilityState: 'visible'};
globalThis.location = {hostname: 'localhost', search: ''};
const state = {orders: [], accounts: [], chats: {}};
const response = (data, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => data});
const ready = () => resetStore({phase: 'ready', workspace: state, auth: {mode: 'local'}});
const requests = [];
const route = handlers => { globalThis.fetch = async (url, options) => { requests.push({url, body: options?.body && JSON.parse(options.body)}); return handlers(url, options); }; };

// A confirmed send resolves with the result and carries the request ID.
ready();
route(() => response({state: {...state, chats: {a: [{role: 'customer', text: 'hi'}]}}, csrf: 't', result: {proposals: []}}));
assert.deepEqual(await sendChat('a', 'hi', 'req-0001'), {proposals: []});
assert.equal(requests.at(-1).body.requestId, 'req-0001');
assert.equal(getStore().chatPending, null);

// A retry of a stored message is confirmed as a duplicate, not run again.
route(() => response({state, csrf: 't', result: {duplicate: true, messageIndex: 0}}));
assert.deepEqual(await sendChat('a', 'hi', 'req-0001'), {duplicate: true, messageIndex: 0});

// A 401 during chat throws (the caller keeps the message) and starts sign-in again.
ready();
route(url => url === '/api/auth' ? response({}, 404) : response({error: 'Sign in again.'}, 401));
await assert.rejects(sendChat('a', 'hi', 'req-0002'), e => e.expired && /session expired/.test(e.message));
assert.equal(getStore().workspace === state, false);  // the old user's workspace is gone

// The server refusing the message (nothing stored) throws without a reload.
ready();
requests.length = 0;
route(() => response({error: 'Your agent is still working on your last message.'}, 400));
await assert.rejects(sendChat('a', 'hi', 'req-0003'), e => e.refused && /still working/.test(e.message));
assert.deepEqual(requests.map(r => r.url), ['/api/action']);

// A lost connection reloads the workspace and says resending is safe.
ready();
requests.length = 0;
route(url => { if (url === '/api/action') throw Error('reset'); return response({state, csrf: 't'}); });
await assert.rejects(sendChat('a', 'hi', 'req-0004'), e => e.lost && /safe: it will not run twice/.test(e.message));
assert.deepEqual(requests.map(r => r.url), ['/api/action', '/api/state?main']);
assert.equal(getStore().chatPending, null);

// A server error after the turn may have stored the message: also "lost", so the same ID is resent.
ready();
route(url => url === '/api/action' ? response({error: 'Operation failed.'}, 500) : response({state, csrf: 't'}));
await assert.rejects(sendChat('a', 'hi', 'req-0005'), e => e.lost);

// Drafts: per user and agent, with the request ID of an unconfirmed send.
const me = draftOwner({mode: 'google', user: {email: 'me@example.com'}});
const other = draftOwner({mode: 'google', user: {email: 'other@example.com'}});
assert.equal(draftOwner({mode: 'local', user: null}), 'local:');
saveDraft(me, 'a', {text: 'Put $10 on Cleveland', requestId: 'req-0006', sentText: 'Put $10 on Cleveland'});
assert.deepEqual(loadDraft(me, 'a'), {text: 'Put $10 on Cleveland', requestId: 'req-0006', sentText: 'Put $10 on Cleveland'});
assert.deepEqual(loadDraft(other, 'a'), {text: '', requestId: null, sentText: null});  // never shown to another user
assert.deepEqual(loadDraft(me, 'b'), {text: '', requestId: null, sentText: null});
// The unchanged message reuses its ID; an edited one is a new message.
assert.equal(requestIdFor(loadDraft(me, 'a'), 'Put $10 on Cleveland', () => 'new'), 'req-0006');
assert.equal(requestIdFor(loadDraft(me, 'a'), 'Put $20 on Cleveland', () => 'new'), 'new');
assert.equal(requestIdFor(loadDraft(me, 'b'), 'anything', () => 'new'), 'new');
clearDraft(me, 'a');
assert.equal(memory.has('governtrade-chat-draft'), false);
// Blocked storage never breaks the page.
globalThis.sessionStorage = {getItem() { throw Error('blocked'); }, setItem() { throw Error('blocked'); }, removeItem() { throw Error('blocked'); }};
saveDraft(me, 'a', {text: 'x'});
assert.deepEqual(loadDraft(me, 'a'), {text: '', requestId: null, sentText: null});
console.log('Chat send tests passed: confirmed, duplicate, 401, refused, lost connection, server error, drafts');
