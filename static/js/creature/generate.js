// Agent creatures: a deterministic SVG drawn from a seed, a market focus (the accessory) and a mood.
// Pure functions only (no DOM), so this module also runs under Node for tests. Signed off in the creature lab.

export const PALETTE = [
  {name: 'Cobalt', hex: '#5B7FE8'}, {name: 'Iris', hex: '#8C73E6'}, {name: 'Orchid', hex: '#C467C2'}, {name: 'Rose', hex: '#D9668F'},
  {name: 'Sky', hex: '#3893C9'}, {name: 'Lagoon', hex: '#2F98A6'}, {name: 'Cocoa', hex: '#AC7D58'}, {name: 'Slate', hex: '#7F8EA8'},
];
// Superellipse bodies: half-width a, half-height b, exponent n, egg taper, lean.
const SHAPES = [
  {name: 'Blob', a: 38.1, b: 34.7, n: 2.3, egg: .06, lean: 0},
  {name: 'Squircle', a: 37.0, b: 34.7, n: 4.2, egg: 0, lean: 0},
  {name: 'Pebble', a: 33.6, b: 39.2, n: 2.1, egg: .16, lean: 0},
  {name: 'Loaf', a: 43.7, b: 30.2, n: 3.6, egg: .05, lean: 0},
  {name: 'Pill', a: 29.1, b: 41.4, n: 3.4, egg: .02, lean: 0},
  {name: 'Bean', a: 38.1, b: 35.8, n: 2.6, egg: .1, lean: .14},
];
const EYES = [{name: 'round', rx: 8.6, ry: 9}, {name: 'tall', rx: 7.4, ry: 10.4}, {name: 'wide', rx: 9.6, ry: 7.8}];
const TOPS = ['none', 'sprout', 'ears', 'antenna', 'tuft'];

export const FOCI = [
  {id: 'general', label: 'General'}, {id: 'crypto', label: 'Crypto'}, {id: 'sports', label: 'Sports'},
  {id: 'politics', label: 'Politics'}, {id: 'economics', label: 'Economics'}, {id: 'culture', label: 'Culture'},
];
export const MOODS = ['idle', 'thinking', 'proposed', 'waiting', 'filled', 'trimmed', 'blocked', 'paused', 'offline'];
// Moods that play once when the state changes; the rest loop.
export const ONE_SHOT = new Set(['proposed', 'filled', 'trimmed']);

const INK = '#26231f';
const SEED_ALPHABET = 'abcdefghijklmnopqrstuvwxyz0123456789';
const MOOD_WORD = {idle: 'idle', thinking: 'thinking', proposed: 'sending a proposal', waiting: 'waiting for you',
  filled: 'happy', trimmed: 'shrugging', blocked: 'sulking', paused: 'asleep', offline: 'dazed'};

function hash(s) {
  let h = 1779033703 ^ s.length;
  for (let i = 0; i < s.length; i++) {
    h = Math.imul(h ^ s.charCodeAt(i), 3432918353);
    h = h << 13 | h >>> 19;
  }
  return () => {
    h = Math.imul(h ^ h >>> 16, 2246822507);
    h = Math.imul(h ^ h >>> 13, 3266489909);
    return (h ^= h >>> 16) >>> 0;
  };
}

function rng(seed) {
  let a = hash(seed)();
  return () => {
    a |= 0; a = a + 0x6D2B79F5 | 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

const pick = (r, arr) => arr[Math.floor(r() * arr.length)];

// A fresh 8-character seed (the server accepts 6 to 12 of [a-z0-9]).
export function newSeed(random = n => crypto.getRandomValues(new Uint8Array(n))) {
  return Array.from(random(8), byte => SEED_ALPHABET[byte % SEED_ALPHABET.length]).join('');
}

// A seed whose colour none of the given agents use yet, when one can be found.
export function freshSeed(takenColors = [], random) {
  let seed = newSeed(random);
  for (let i = 0; i < 40 && takenColors.includes(spec(seed).color.hex); i++) seed = newSeed(random);
  return seed;
}

export function spec(seed) {
  const r = rng(seed);
  return {
    seed, color: pick(r, PALETTE), shape: pick(r, SHAPES), eyes: pick(r, EYES), pupil: r() < .5 ? 2.7 : 3.4,
    gap: r() * 1.6, top: pick(r, TOPS), cheeks: r() < .5, blink: (r() * 4).toFixed(2),
  };
}

// An agent saved before looks existed still gets a stable creature, derived from its id.
export function lookFor(agent) {
  if (agent?.look?.seed) return agent.look;
  const r = rng(String(agent?.id || 'agent'));
  return {seed: Array.from({length: 8}, () => SEED_ALPHABET[Math.floor(r() * SEED_ALPHABET.length)]).join(''), focus: 'general'};
}

export const colorOf = look => spec(look.seed).color;

function bodyPath(s) {
  const {a, b, n, egg, lean} = s.shape, cy = 104 - b, N = 40, pts = [];
  for (let i = 0; i < N; i++) {
    const t = i / N * Math.PI * 2, c = Math.cos(t), sn = Math.sin(t);
    let x = a * Math.sign(c) * Math.abs(c) ** (2 / n);
    const y = b * Math.sign(sn) * Math.abs(sn) ** (2 / n);
    x *= y < 0 ? 1 - egg * (-y / b) : 1 + egg * .4 * (y / b);
    x += lean * (-y) * .5;
    pts.push([60 + x, cy + y]);
  }
  const f = v => v.toFixed(2);
  let d = `M${f(pts[0][0])} ${f(pts[0][1])}`;
  for (let i = 0; i < N; i++) {
    const p0 = pts[(i - 1 + N) % N], p1 = pts[i], p2 = pts[(i + 1) % N], p3 = pts[(i + 2) % N];
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += `C${f(c1[0])} ${f(c1[1])} ${f(c2[0])} ${f(c2[1])} ${f(p2[0])} ${f(p2[1])}`;
  }
  return d + 'Z';
}

// Lids carry the expression: lid = share of the eye covered, tilt > 0 lowers the inner corners.
const FACE = {
  idle: {lid: [.22, .22], tilt: [0, 0], mouth: 'smile'},
  thinking: {lid: [.3, .12], tilt: [6, -4], px: 2.2, py: -2.2, mouth: 'o'},
  proposed: {lid: [.04, .04], tilt: [-4, -4], px: 2.4, mouth: 'open', fillMouth: true},
  waiting: {lid: [.5, .5], tilt: [0, 0], px: 2, py: .6, mouth: 'flat'},
  filled: {lid: [.1, .1], tilt: [-6, -6], happy: true, mouth: 'grin', fillMouth: true},
  trimmed: {lid: [.52, .1], tilt: [-8, 6], px: -1.6, mouth: 'wavy'},
  blocked: {lid: [.46, .46], tilt: [16, 16], py: .6, mouth: 'frown'},
  paused: {closed: true, mouth: 'o'},
  offline: {lid: [.36, .18], tilt: [-6, 8], swirl: true, mouth: 'zig'},
};
const MOUTH = {
  smile: (x, y) => `M${x - 4} ${y}Q${x} ${y + 3.6} ${x + 4} ${y}`,
  o: (x, y) => `M${x + 2.6} ${y + 1}a1.6 1.6 0 1 0 .01 0Z`,
  open: (x, y) => `M${x - 4.5} ${y - .5}Q${x} ${y + 6} ${x + 4.5} ${y - .5}Z`,
  flat: (x, y) => `M${x - 3.5} ${y + 1}H${x + 3.5}`,
  grin: (x, y) => `M${x - 6} ${y - 1}Q${x} ${y + 8} ${x + 6} ${y - 1}Z`,
  wavy: (x, y) => `M${x - 5} ${y + 1}q1.25 -2 2.5 0t2.5 0t2.5 0t2.5 0`,
  frown: (x, y) => `M${x - 4} ${y + 3}Q${x} ${y - 1} ${x + 4} ${y + 3}`,
  zig: (x, y) => `M${x - 5} ${y + 1}l2.5 -2 2.5 2 2.5 -2 2.5 2`,
};
// Calm moods fade the body toward grey.
const DIM = {blocked: .72, paused: .82, offline: .6};

const escapeAttr = v => String(v).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

let uid = 0;

// The creature as an SVG string. label: when given, the SVG is an image with that name; otherwise decorative.
export function creatureSvg(seed, {mood = 'idle', focus = 'general', size = 48, track = false, poke = false, live = false, label} = {}) {
  const s = spec(seed), {a, b, lean} = s.shape, cy = 104 - b, top = cy - b, topX = 60 + lean * b * .5, id = 'cr' + (++uid);
  const fill = DIM[mood] ? `color-mix(in oklab,${s.color.hex} ${DIM[mood] * 100}%,#a9a49a)` : s.color.hex;
  const dark = `color-mix(in oklab,${fill} 72%,${INK})`;
  const {rx, ry} = s.eyes, fx = 60 + lean * b * .12, ey = cy - b * .1, sx = rx + .2 + s.gap * .6, my = ey + ry + 5;
  const L = fx - sx, R = fx + sx, path = bodyPath(s);

  // Crypto and Sports wear things on their heads, so they skip the top feature; Culture keeps only ears.
  let topFeat = s.top;
  if (focus === 'sports' || focus === 'culture') topFeat = topFeat === 'ears' ? 'ears' : 'none';
  if (focus === 'crypto') topFeat = 'none';

  const back = [], front = [], extras = [];
  if (topFeat === 'ears') back.push(`<circle cx="${60 - a * .56}" cy="${top + 9}" r="8" fill="${dark}" stroke="${INK}" stroke-width="2.6"/><circle cx="${60 + a * .56}" cy="${top + 9}" r="8" fill="${dark}" stroke="${INK}" stroke-width="2.6"/>`);
  if (topFeat === 'sprout') front.push(`<path d="M${topX} ${top + 2}q-1 -7 2 -11" stroke="${dark}" stroke-width="2.4" fill="none" stroke-linecap="round"/><ellipse cx="${topX + 6}" cy="${top - 10}" rx="6" ry="3.4" transform="rotate(-28 ${topX + 6} ${top - 10})" fill="${dark}" stroke="${INK}" stroke-width="2"/>`);
  if (topFeat === 'tuft') front.push(`<path d="M${topX - 5} ${top + 3}q1 -7 4 -8M${topX} ${top + 2}q0 -8 3 -10M${topX + 5} ${top + 3}q0 -6 4 -7" stroke="${dark}" stroke-width="2.2" fill="none" stroke-linecap="round"/>`);
  if (topFeat === 'antenna') front.push(`<path d="M${topX} ${top + 2}V${top - 10}" stroke="${INK}" stroke-width="2.2" stroke-linecap="round"/><circle cx="${topX}" cy="${top - 13}" r="3.6" fill="${dark}" stroke="${INK}" stroke-width="2"/>`);

  let acc = '';
  if (focus === 'sports') acc += `<g clip-path="url(#${id}b)"><rect x="-2" y="${top + b * .2}" width="124" height="7" fill="#fff" stroke="${INK}" stroke-width="1.8"/><rect x="0" y="${top + b * .2 + 2.6}" width="120" height="1.8" fill="${s.color.hex}" opacity=".9"/></g>`;
  if (focus === 'culture') acc += `<ellipse cx="${topX - 3}" cy="${top + 3}" rx="${a * .62}" ry="7" transform="rotate(-9 ${topX - 3} ${top + 3})" fill="${INK}"/><circle cx="${topX - 1}" cy="${top - 4.5}" r="2.2" fill="${INK}"/>`;
  if (focus === 'crypto') {
    const hx = a * .97, hy = cy - b * .08, arc = `M${60 - hx} ${hy}C${60 - hx} ${top - 14} ${60 + hx} ${top - 14} ${60 + hx} ${hy}`;
    front.push(`<path d="${arc}" fill="none" stroke="${INK}" stroke-width="6" stroke-linecap="round"/><path d="${arc}" fill="none" stroke="#3a3f4b" stroke-width="2.6" stroke-linecap="round"/><rect x="${60 - hx - 5}" y="${hy - 9}" width="10" height="18" rx="4.5" fill="#3a3f4b" stroke="${INK}" stroke-width="2.2"/><rect x="${60 + hx - 5}" y="${hy - 9}" width="10" height="18" rx="4.5" fill="#3a3f4b" stroke="${INK}" stroke-width="2.2"/><circle cx="${60 + hx}" cy="${hy}" r="2" fill="${fill}"/>`);
    const c0 = my + 6.5, chain = `M${fx - 13} ${c0 - 3}Q${fx} ${c0 + 12} ${fx + 13} ${c0 - 3}`;
    acc += `<path d="${chain}" fill="none" stroke="${INK}" stroke-width="3.4" stroke-linecap="round"/><path d="${chain}" fill="none" stroke="#d9dce2" stroke-width="1.6" stroke-linecap="round" stroke-dasharray="2.2 1.4"/><g class="coin"><circle cx="${fx}" cy="${c0 + 10}" r="5.2" fill="#d9dce2" stroke="${INK}" stroke-width="2"/><circle cx="${fx}" cy="${c0 + 10}" r="2.4" fill="none" stroke="${INK}" stroke-width="1.1"/></g>`;
  }
  if (focus === 'politics') {
    const st = Math.max(my + 6, cy + b * .4), nx = fx;
    acc += `<g clip-path="url(#${id}b)"><rect x="0" y="${st}" width="120" height="70" fill="#2c3242"/><path d="M${nx - 9} ${st}L${nx} ${st + 15}L${nx + 9} ${st}Z" fill="#fff"/><path d="M${nx - 2.4} ${st}h4.8l-.9 3.2h-3z" fill="${fill}"/><path d="M${nx - 1.6} ${st + 3.2}h3.2l1.5 8.6-3.1 3.4-3.1-3.4z" fill="${fill}"/><path d="M${nx - 9} ${st}l-5.5 7.5 7 5.5M${nx + 9} ${st}l5.5 7.5-7 5.5" stroke="#171b26" stroke-width="1.3" fill="none" stroke-linejoin="round"/><circle cx="${nx - 15}" cy="${st + 11.5}" r="1.5" fill="#e9e4d8"/><circle cx="${nx}" cy="${st + 20}" r="1.2" fill="#171b26"/><circle cx="${nx}" cy="${st + 26}" r="1.2" fill="#171b26"/></g>`;
  }

  const pose = FACE[mood] || FACE.idle;
  const eye = (x, i) => {
    if (pose.closed) return `<path d="M${x - rx} ${ey}Q${x} ${ey + ry * .8} ${x + rx} ${ey}" stroke="${INK}" stroke-width="2" fill="none" stroke-linecap="round"/>`;
    const cover = pose.lid[i], angle = i === 0 ? pose.tilt[0] : -pose.tilt[1], edge = (ey - ry + cover * 2 * ry).toFixed(2), clip = `${id}e${i}`;
    const py = (pose.py || 0) + cover * ry * .5;
    const pupil = pose.swirl
      ? `<g class="swirl"><path d="M${x} ${ey + py}m0 -.8a.8 .8 0 1 1 -.8 .8a1.8 1.8 0 0 1 1.8 -1.8a2.8 2.8 0 0 1 2.8 2.8a3.8 3.8 0 0 1 -3.8 3.8" stroke="${INK}" stroke-width="1.4" fill="none" stroke-linecap="round"/></g>`
      : `<g class="pupil" style="--px:${pose.px || 0}px;--py:${py.toFixed(2)}px"><circle cx="${x}" cy="${ey}" r="${s.pupil}" fill="${INK}"/></g>`;
    const lower = pose.happy ? `<ellipse cx="${x}" cy="${ey + ry * 1.5}" rx="${rx * 1.6}" ry="${ry * 1.05}" fill="${fill}" stroke="${INK}" stroke-width="2.3"/>` : '';
    return `<g class="eye"><clipPath id="${clip}"><ellipse cx="${x}" cy="${ey}" rx="${rx}" ry="${ry}"/></clipPath><ellipse cx="${x}" cy="${ey}" rx="${rx}" ry="${ry}" fill="#fff"/><g clip-path="url(#${clip})">${pupil}<g class="lid" style="--bd:${s.blink}s;--bdy:${(2 * ry * (1 - cover) + 3).toFixed(2)}px"><g transform="rotate(${angle} ${x} ${edge})"><rect x="${x - rx - 6}" y="${(edge - 30).toFixed(2)}" width="${2 * rx + 12}" height="30" fill="${fill}"/><path d="M${x - rx - 6} ${edge}H${x + rx + 6}" stroke="${INK}" stroke-width="2.3"/></g></g>${lower}</g><ellipse cx="${x}" cy="${ey}" rx="${rx}" ry="${ry}" fill="none" stroke="${INK}" stroke-width="2.1"/></g>`;
  };
  const cheeks = s.cheeks && !DIM[mood] ? `<ellipse cx="${L - rx * .5}" cy="${ey + ry + 2.5}" rx="3.6" ry="2" fill="#ff8fb1" opacity=".5"/><ellipse cx="${R + rx * .5}" cy="${ey + ry + 2.5}" rx="3.6" ry="2" fill="#ff8fb1" opacity=".5"/>` : '';
  const big = Math.max(rx, ry);
  const monocle = focus === 'economics' && !pose.closed ? `<circle cx="${R}" cy="${ey}" r="${big + 2.6}" fill="#fff" fill-opacity=".14" stroke="${INK}" stroke-width="2"/><path d="M${R + big + 2} ${ey + 3}q4 9 -1 16" stroke="${INK}" stroke-width="1" fill="none"/>` : '';
  const mouthStyle = pose.fillMouth ? `fill="${INK}"` : `fill="none" stroke="${INK}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"`;
  const face = `<g class="face">${cheeks}${eye(L, 0)}${eye(R, 1)}${monocle}<path transform="translate(${fx} ${my}) scale(1.35) translate(${-fx} ${-my})" d="${MOUTH[pose.mouth](fx, my)}" ${mouthStyle}/></g>`;

  if (mood === 'thinking') extras.push(`<g transform="translate(${topX + a * .55} ${top - 6})"><circle class="tdot" cx="0" cy="0" r="2.4" fill="${INK}"/><circle class="tdot" cx="7" cy="-3" r="2.4" fill="${INK}"/><circle class="tdot" cx="14" cy="-6" r="2.4" fill="${INK}"/></g>`);
  if (mood === 'paused') extras.push(`<g class="zz" transform="translate(${topX + a * .45} ${top - 2})"><text>z</text><text>z</text><text>z</text></g>`);
  if (mood === 'offline') {
    let stars = '';
    for (let i = 0; i < 3; i++) {
      const t = i / 3 * Math.PI * 2;
      stars += `<path transform="translate(${(Math.cos(t) * 15).toFixed(2)} ${(Math.sin(t) * 15).toFixed(2)})" d="M0 -3.4L.9 -.9 3.4 0 .9 .9 0 3.4 -.9 .9 -3.4 0 -.9 -.9Z" fill="${INK}"/>`;
    }
    extras.push(`<g transform="translate(${topX} ${top - 9}) scale(1 .38)"><g class="orbit">${stars}</g></g>`);
  }
  if (mood === 'proposed') extras.push(`<g class="ticket"><rect x="${60 + a * .55}" y="${cy - 4}" width="16" height="11" rx="2" fill="#fff" stroke="${INK}" stroke-width="1.3"/><path d="M${60 + a * .55 + 3.5} ${cy}h9M${60 + a * .55 + 3.5} ${cy + 3.5}h6" stroke="${INK}" stroke-width="1.1" stroke-linecap="round"/></g>`);

  const shade = `<g clip-path="url(#${id}b)"><ellipse cx="${60 + a * .62}" cy="${cy + b * .3}" rx="${a * .72}" ry="${b * 1.05}" fill="${INK}" opacity=".16"/><ellipse cx="${60 - a * .46}" cy="${cy - b * .5}" rx="${a * .13}" ry="${b * .08}" transform="rotate(-30 ${60 - a * .46} ${cy - b * .5})" fill="#fff" opacity=".6"/><circle cx="${60 - a * .3}" cy="${cy - b * .66}" r="1.6" fill="#fff" opacity=".6"/></g>`;
  const outline = `<path d="${path}" fill="none" stroke="${INK}" stroke-width="2.8" stroke-linejoin="round"/>`;
  const a11y = label ? `role="img" aria-label="${escapeAttr(label)}, ${MOOD_WORD[mood] || mood}"` : 'aria-hidden="true" focusable="false"';
  const classes = `cr m-${mood}${live ? ' live' : ''}`;
  return `<svg class="${classes}" viewBox="0 0 120 120" width="${size}" height="${size}" ${a11y} data-mood="${mood}"${track ? ' data-track' : ''}${poke ? ' data-poke' : ''}><defs><clipPath id="${id}b"><path d="${path}"/></clipPath></defs><ellipse class="shadow" cx="62" cy="106.5" rx="${a * .86}" ry="3.4" fill="${INK}" opacity=".22"/><g class="wig"><g class="rig"><g class="body">${back.join('')}<path d="${path}" fill="${fill}"/>${shade}${acc}${outline}${front.join('')}${face}</g></g></g>${extras.join('')}</svg>`;
}
