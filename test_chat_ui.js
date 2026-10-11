// Run with node test_chat_ui.js. No browser, venue or model credentials required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = vm.createContext({assert, console, setTimeout, clearTimeout, clearInterval,
  document: {addEventListener() {}, querySelector() { return null; }}});
const source = fs.readFileSync('static/app.js', 'utf8').replace('});initializeAuth();', '});');
vm.runInContext(source, context);
vm.runInContext(`
(async () => {
  render = () => {};
  let resolveChat;
  fetch = () => new Promise(resolve => {resolveChat = resolve});
  state = {chats: {agent: []}};agent = 'agent';chatDraft = 'old secret';
  const pending = chatSend('Put $20 on Cleveland');
  resetUserView();
  assert.equal(chatDraft, '');
  assert.equal(chatPending, null);
  assert.equal(chatBusy, false);
  state = {newUser: true};chatDraft = 'new draft';
  resolveChat({ok: true, json: async () => ({state: {oldUser: true}})});
  await pending;
  assert.equal(state.newUser, true);
  assert.equal(chatDraft, 'new draft');
  state = {chatProposals: {agent: [{status: 'executed', order: {status: 'awaiting approval'}}]}};
  assert.equal(chatPolling(), true);
  state.chatProposals.agent[0].order.status = 'filled';
  assert.equal(chatPolling(), false);
  let poll;
  setTimeout = fn => {poll = fn;return 1};
  load = () => {throw Error('Polling must not refresh Kalshi')};
  state.chatProposals.agent[0].order.status = 'awaiting approval';
  fetch = async () => ({ok: true, json: async () => ({chatProposals: {agent: [{status: 'executed', order: {status: 'filled'}}]}})});
  schedulePoll();await poll();
  assert.equal(state.chatProposals.agent[0].order.status, 'filled');
  // A restart mid-turn: the proxy answers with an HTML error page, then the reload shows the saved message.
  agent = 'agent';
  load = async () => {state = {chats: {agent: [{role: 'customer', text: 'Put $5 on Cleveland'}]}}};
  fetch = async () => ({ok: false, status: 502, json: async () => {throw SyntaxError("Unexpected token '<'")}});
  await chatSend('Put $5 on Cleveland');
  assert.ok(error.includes("unavailable while your agent was working (HTTP 502)"), error);
  assert.equal(chatDraft, '');  // the message was saved, so it is not offered again
  assert.equal(chatBusy, false);
  // A dropped connection keeps the unsent text for another try.
  load = async () => {state = {chats: {agent: []}}};
  fetch = async () => {throw TypeError('Failed to fetch')};
  await chatSend('Put $6 on Cleveland');
  assert.equal(error, CHAT_LOST);
  assert.equal(chatDraft, 'Put $6 on Cleveland');
  // Other actions explain an unavailable server instead of showing a JSON parse error.
  busy = false;
  fetch = async () => ({ok: false, status: 503, json: async () => {throw SyntaxError("Unexpected token '<'")}});
  await api('refresh');
  assert.ok(error.includes("unavailable right now (HTTP 503)"), error);
  console.log('Chat UI: session reset, stale response, approval polling, lightweight updates and lost connections pass.');
})()
`, context).catch(error => {console.error(error);process.exitCode = 1});
