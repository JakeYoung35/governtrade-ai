// Run with node tests/javascript/test_creature.mjs. Creature generation and agent moods; no browser needed.
import assert from 'node:assert/strict';
import {creatureSvg, freshSeed, lookFor, newSeed, spec, PALETTE, FOCI, MOODS, colorOf} from '../../static/js/creature/generate.js';
import {moodFor} from '../../static/js/creature/moods.js';

const withoutIds = svg => svg.replace(/cr\d+/g, 'cr');

// The same seed always draws the same creature.
assert.equal(withoutIds(creatureSvg('k3v9qa', {mood: 'waiting', focus: 'sports'})), withoutIds(creatureSvg('k3v9qa', {mood: 'waiting', focus: 'sports'})));
assert.notEqual(withoutIds(creatureSvg('k3v9qa')), withoutIds(creatureSvg('m81zpd')));

// Every mood and focus renders a complete SVG with no missing numbers.
for (const mood of MOODS) for (const {id: focus} of FOCI) for (const seed of ['k3v9qa', 'm81zpd', 'x0r2tt', 'w7yb4e', 'zz9y8x7w']) {
  const svg = creatureSvg(seed, {mood, focus, size: 40});
  assert.ok(svg.startsWith('<svg') && svg.endsWith('</svg>'), `${seed} ${mood} ${focus}`);
  assert.ok(!/undefined|NaN/.test(svg), `${seed} ${mood} ${focus} has a missing value`);
  assert.ok(svg.includes(`class="cr m-${mood}`));
}

// Agent colours never use the governor's verdict colours.
const verdicts = ['#1f7a55', '#8a5a00', '#b3372f'];
for (const p of PALETTE) assert.ok(!verdicts.includes(p.hex.toLowerCase()), p.name);
for (let i = 0; i < 200; i++) assert.ok(PALETTE.includes(spec('seed' + i).color));

// Seeds match what the server accepts, and a fresh seed avoids colours other agents use when it can.
for (let i = 0; i < 50; i++) assert.match(newSeed(), /^[a-z0-9]{8}$/);
const taken = PALETTE.slice(0, 7).map(p => p.hex);
for (let i = 0; i < 20; i++) assert.equal(colorOf({seed: freshSeed(taken)}).hex, PALETTE[7].hex);
assert.match(freshSeed(PALETTE.map(p => p.hex)), /^[a-z0-9]{8}$/);  // all taken: still a valid seed

// Agents saved before looks existed get a stable look from their id.
assert.deepEqual(lookFor({id: 'abc', look: {seed: 'k3v9qa', focus: 'crypto'}}), {seed: 'k3v9qa', focus: 'crypto'});
assert.deepEqual(lookFor({id: 'agent-1'}), lookFor({id: 'agent-1'}));
assert.match(lookFor({id: 'agent-1'}).seed, /^[a-z0-9]{8}$/);
assert.equal(lookFor({id: 'agent-1'}).focus, 'general');

// Accessible name only when the creature stands alone; labels are escaped.
assert.ok(creatureSvg('k3v9qa').includes('aria-hidden="true"'));
const labelled = creatureSvg('k3v9qa', {label: 'Rally <3', mood: 'paused'});
assert.ok(labelled.includes('role="img" aria-label="Rally &lt;3, asleep"'));
assert.ok(creatureSvg('k3v9qa', {mood: 'filled', live: true}).includes('class="cr m-filled live"'));

// Moods follow what is happening to the agent, in priority order.
const now = Date.parse('2026-10-14T12:00:00Z');
const agent = {id: 'a1', accountId: 'acc', status: 'active'};
// The server summarises each agent's orders (order_views.agent_activity); this builds that summary from a list.
const activityOf = (id, orders) => {
  const own = orders.filter(o => o.agent === id);
  const last = own.reduce((latest, o) => (!latest || Date.parse(o.time) > Date.parse(latest.time) ? o : latest), null);
  return {awaiting: own.filter(o => o.status === 'awaiting approval').length, lastOrder: last && {time: last.time, decision: last.decision, status: last.status}};
};
const ws = (orders = [], extra = {}) => ({accounts: [{id: 'acc', status: 'connected'}], permissions: {automation: true},
  agentConfigs: ['a1', 'a2'].map(id => ({id, activity: activityOf(id, orders)})), ...extra});
assert.equal(moodFor(agent, ws(), {now}), 'idle');
assert.equal(moodFor(agent, ws(), {now, thinking: true}), 'thinking');
assert.equal(moodFor(agent, ws([{agent: 'a1', status: 'awaiting approval', time: '2026-10-14T11:00:00Z'}]), {now}), 'waiting');
assert.equal(moodFor(agent, ws([{agent: 'a2', status: 'awaiting approval', time: '2026-10-14T11:00:00Z'}]), {now}), 'idle');
assert.equal(moodFor(agent, ws([{agent: 'a1', status: 'blocked', decision: 'BLOCK', time: '2026-10-14T11:55:00Z'}]), {now}), 'blocked');
assert.equal(moodFor(agent, ws([{agent: 'a1', status: 'blocked', decision: 'BLOCK', time: '2026-10-14T11:40:00Z'}]), {now}), 'idle');
assert.equal(moodFor(agent, ws([
  {agent: 'a1', status: 'blocked', decision: 'BLOCK', time: '2026-10-14T11:55:00Z'},
  {agent: 'a1', status: 'filled', decision: 'ALLOW', time: '2026-10-14T11:58:00Z'},
]), {now}), 'idle');
assert.equal(moodFor({...agent, status: 'paused'}, ws([{agent: 'a1', status: 'awaiting approval', time: '2026-10-14T11:00:00Z'}]), {now}), 'paused');
assert.equal(moodFor(agent, ws([], {permissions: {automation: false}}), {now}), 'paused');
assert.equal(moodFor(agent, {...ws(), accounts: [{id: 'acc', status: 'connected', error: 'Kalshi did not answer.'}]}, {now}), 'offline');

console.log('Creatures: determinism, every mood and focus, palette, seeds, legacy looks, accessibility and moods pass.');
