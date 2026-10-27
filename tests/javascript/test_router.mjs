// match(): page paths to screens. A malformed escape in a path is an unknown page, never a crash while rendering.
import assert from 'node:assert/strict';
globalThis.addEventListener = () => {};
globalThis.document = {addEventListener() {}};
const {match} = await import('../../static/js/router.js');

assert.deepEqual(match('/'), {name: 'home', params: {}});
assert.deepEqual(match('/agents/new'), {name: 'new-agent', params: {}});
assert.deepEqual(match('/agents/a%20b/'), {name: 'agent', params: {id: 'a b'}});
assert.deepEqual(match('/agents/%E0%A4%A'), {name: 'not-found', params: {}});
assert.deepEqual(match('/agents/%'), {name: 'not-found', params: {}});
assert.deepEqual(match('/nowhere'), {name: 'not-found', params: {}});
console.log('Router tests passed: routes, params, malformed escapes');
