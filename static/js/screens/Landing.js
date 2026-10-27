// The landing page for visitors who are not in a workspace yet. The agents act out what the product does: they pitch
// trades, the governor allows, holds or blocks each one. The way in depends on the deployment (local entry, Google or
// Auth0), rendered by `action(size)`; a Google button can only mount once, so `repeatable` says whether the nav and
// closer may render it again.
import {useEffect, useRef, useState} from 'preact/hooks';
import {html} from '../html.js';
import {BrandMark} from '../ui/icons.js';
import {Creature} from '../creature/Creature.js';
import {spec, PALETTE} from '../creature/generate.js';
import {reducedMotion} from '../creature/motion.js';

// One seed per palette colour, found deterministically, so the cast looks the same on every visit and never repeats a colour.
const SEEDS = (() => {
  const byColor = new Map();
  for (let i = 0; byColor.size < PALETTE.length && i < 5000; i++) {
    const seed = 'gt' + i.toString(36).padStart(4, '0');
    const hex = spec(seed).color.hex;
    if (!byColor.has(hex)) byColor.set(hex, seed);
  }
  return [...byColor.values()];
})();
const look = (i, focus) => ({seed: SEEDS[i % SEEDS.length], focus});

const CREW = [
  {name: 'Scout', look: look(0, 'sports')}, {name: 'Ledger', look: look(1, 'economics')}, {name: 'Satoshi', look: look(2, 'crypto')},
  {name: 'Senator', look: look(4, 'politics')}, {name: 'Muse', look: look(3, 'culture')},
];
// Sample proposals for the scene. The verdicts mirror the governor's real outcomes; the markets are illustrative.
const PITCHES = [
  {who: 0, ticket: 'YES KC-WIN ×40', verdict: 'ALLOW', note: 'sized 40 → 32', after: 'filled'},
  {who: 1, ticket: 'NO FED-CUT ×120', verdict: 'REVIEW', note: 'over your approval gate', after: 'waiting'},
  {who: 2, ticket: 'YES BTC-100K ×300', verdict: 'BLOCK', note: 'negative edge after fees', after: 'blocked'},
  {who: 3, ticket: 'YES SENATE ×25', verdict: 'ALLOW', note: 'within your daily cap', after: 'filled'},
  {who: 4, ticket: 'NO OSCAR-BP ×60', verdict: 'ALLOW', note: 'trimmed to cap: 45', after: 'trimmed'},
];
const ROSTER = [
  ['general', 'Generalist', 'Anything with an edge'], ['sports', 'Sports', 'Games, spreads, props'], ['crypto', 'Crypto', 'BTC and ETH thresholds'],
  ['politics', 'Politics', 'Races and control'], ['economics', 'Economics', 'Fed, CPI, jobs'], ['culture', 'Culture', 'Awards, charts, box office'],
];
const ROSTER_MOODS = ['idle', 'thinking', 'waiting', 'idle', 'paused'];
const STEPS = [
  ['01', 'Hire agents', 'Pick a model or plug in your own. Give each one a market focus and a budget.', [['idle', 'sports'], ['thinking', 'crypto'], ['idle', 'politics']]],
  ['02', 'They pitch trades', 'Agents never place orders. They send proposals with a price, a size and their reasoning.', [['proposed', 'economics'], ['thinking', 'general']]],
  ['03', 'The governor rules', 'Edge, Kelly sizing, caps and approval gates decide: allowed, trimmed, held for you, or blocked.', [['filled', 'sports'], ['waiting', 'culture'], ['blocked', 'crypto']]],
];

const smallScreen = () => matchMedia('(max-width: 899px)').matches;
const jumpTo = id => document.getElementById(id)?.scrollIntoView({behavior: reducedMotion() ? 'auto' : 'smooth', block: 'start'});

// Every timer the page starts goes through this, so leaving the page (signing in) stops them all.
function useTimers() {
  const timers = useRef({alive: true, ids: new Set()});
  useEffect(() => () => {
    timers.current.alive = false;
    timers.current.ids.forEach(clearTimeout);
  }, []);
  return timers.current;
}
function wait(timers, ms) {
  return new Promise(resolve => {
    const id = setTimeout(() => { timers.ids.delete(id); resolve(timers.alive); }, ms);
    timers.ids.add(id);
  });
}

function useReveal() {
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || reducedMotion() || !('IntersectionObserver' in window)) return el?.classList.add('in');
    const io = new IntersectionObserver(([entry]) => { if (entry.isIntersecting) { el.classList.add('in'); io.disconnect(); } }, {threshold: .12});
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return ref;
}

function Headline() {
  const words = 'Your markets. Your agents. Your rules.'.split(' ');
  return html`<h1 class="landing-title">${words.map((w, i) => html`<span class="word" style=${`animation-delay:${80 + i * 70}ms`}>${w}</span> `)}</h1>`;
}

// The hero scene: one agent at a time thinks, sends a ticket to the governor, and reacts to the verdict.
function Stage() {
  const timers = useTimers();
  const stage = useRef(null), gov = useRef(null), venue = useRef(null), ticket = useRef(null), seats = useRef([]);
  const [size] = useState(() => smallScreen() ? 56 : 88);
  const [moods, setMoods] = useState(() => CREW.map(() => 'idle'));
  const [ticketText, setTicketText] = useState('');
  const [verdict, setVerdict] = useState(null);
  const [tally, setTally] = useState({ALLOW: 0, REVIEW: 0, BLOCK: 0, filled: 0});
  const [pulse, setPulse] = useState(false);

  useEffect(() => {
    if (reducedMotion()) {
      // A still frame that still tells the story.
      setMoods(['filled', 'waiting', 'blocked', 'idle', 'thinking']);
      setTally({ALLOW: 1, REVIEW: 1, BLOCK: 1, filled: 1});
      return;
    }
    const setMood = (who, mood) => setMoods(all => all.map((m, i) => i === who ? mood : m));
    const center = el => {
      const s = stage.current.getBoundingClientRect(), r = el.getBoundingClientRect();
      return {x: r.left - s.left + r.width / 2, y: r.top - s.top + r.height / 2};
    };
    const fly = (from, to, ms, easing) => {
      const lift = Math.min(from.y, to.y) - 50;
      return ticket.current.animate([
        {transform: `translate(${from.x}px,${from.y}px) translate(-50%,-50%) scale(.5) rotate(-8deg)`, opacity: 0},
        {transform: `translate(${(from.x + to.x) / 2}px,${lift}px) translate(-50%,-50%) scale(1) rotate(4deg)`, opacity: 1, offset: .5},
        {transform: `translate(${to.x}px,${to.y}px) translate(-50%,-50%) scale(.85)`, opacity: 1},
      ], {duration: ms, easing, fill: 'forwards'}).finished.catch(() => {});  // cancelled when the page unmounts
    };

    async function pitch(p, n) {
      setMood(p.who, 'thinking');
      if (!await wait(timers, 1300)) return false;
      setMood(p.who, 'proposed');
      setTicketText(p.ticket);
      if (!await wait(timers, 250)) return false;
      const govAt = center(gov.current.querySelector('svg'));
      await fly(center(seats.current[p.who]), govAt, 900, 'cubic-bezier(.5,0,.3,1)');
      if (!timers.alive) return false;
      setPulse(true);
      if (!await wait(timers, 220)) return false;
      setPulse(false);
      setVerdict({...p, n});
      setTally(t => ({...t, [p.verdict]: t[p.verdict] + 1}));
      if (p.verdict === 'ALLOW') {
        await fly(govAt, center(venue.current), 700, 'cubic-bezier(.3,0,.2,1)');
        if (!timers.alive) return false;
        setTally(t => ({...t, filled: t.filled + 1}));
      } else {
        // A held or blocked ticket drops away below the governor.
        const tilt = p.verdict === 'BLOCK' ? 20 : -6;
        await ticket.current.animate([{opacity: 1}, {opacity: 0, transform: `translate(${govAt.x}px,${govAt.y + 40}px) translate(-50%,-50%) scale(.6) rotate(${tilt}deg)`}],
          {duration: 500, easing: 'ease-in', fill: 'forwards'}).finished.catch(() => {});
        if (!timers.alive) return false;
      }
      ticket.current.getAnimations().forEach(a => a.cancel());
      setMood(p.who, p.after);
      if (!await wait(timers, 2600)) return false;
      setMood(p.who, 'idle');
      return wait(timers, 400);
    }

    (async () => {
      if (!await wait(timers, 1200)) return;
      for (let n = 0; await pitch(PITCHES[n % PITCHES.length], n); n++);
    })();
  }, []);

  return html`<div class="landing-stage" ref=${stage} role="img" aria-label="Agents propose trades; the governor allows, holds for review, or blocks each one">
    <div class="stage-floor"></div>
    <div class="stage-tally" aria-hidden="true">
      <span><b>${tally.ALLOW}</b>allowed</span><span><b>${tally.REVIEW}</b>to review</span><span><b>${tally.BLOCK}</b>blocked</span>
    </div>
    <div class="stage-venue" ref=${venue} aria-hidden="true"><span>KALSHI · demo</span><span>filled <b>${tally.filled}</b></span></div>
    <div class=${'stage-gov' + (pulse ? ' pulse' : '')} ref=${gov} aria-hidden="true">
      <svg viewBox="0 0 24 24"><path d="M12 3 4 7v5c0 4.6 3.4 8.3 8 9 4.6-.7 8-4.4 8-9V7z" fill="currentColor" /><path d="m8.5 12 2.4 2.4 4.6-4.8" stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round" /></svg>
      <span>GOVERNOR</span>
    </div>
    ${verdict ? html`<div key=${verdict.n} class=${'stage-verdict v-' + verdict.verdict.toLowerCase()} aria-hidden="true">${verdict.verdict} · ${verdict.note}</div>` : null}
    <div class="stage-ticket" ref=${ticket} aria-hidden="true">${ticketText}</div>
    <div class="stage-crew">
      ${CREW.map((c, i) => html`<div class="stage-agent" key=${c.name} style=${`animation-delay:${300 + i * 110}ms`} ref=${el => { seats.current[i] = el; }}>
        <${Creature} look=${c.look} mood=${moods[i]} size=${size} track poke />
        <span>${c.name}</span>
      </div>`)}
    </div>
  </div>`;
}

// One creature per market focus, each drifting through calm moods; hovering one makes it happy.
function Roster() {
  const [tick, setTick] = useState(0);
  const [hovered, setHovered] = useState(-1);
  useEffect(() => {
    if (reducedMotion()) return;
    const id = setInterval(() => setTick(t => t + 1), 1400);
    return () => clearInterval(id);
  }, []);
  // Card i changes on every ROSTER.length-th tick, so one card at a time shifts mood.
  const moodOf = i => ROSTER_MOODS[Math.floor((tick + ROSTER.length - 1 - i) / ROSTER.length) % ROSTER_MOODS.length];
  return html`<div class="landing-roster">
    ${ROSTER.map(([focus, name, blurb], i) => html`<div class="roster-card" key=${focus} onMouseEnter=${() => setHovered(i)} onMouseLeave=${() => setHovered(-1)}>
      <${Creature} look=${look(i + 5, focus)} mood=${hovered === i ? 'filled' : moodOf(i)} size=${80} track poke />
      <b>${name}</b><span>${blurb}</span>
    </div>`)}
  </div>`;
}

function Steps() {
  const [replay, setReplay] = useState(0);
  useEffect(() => {
    if (reducedMotion()) return;
    const id = setInterval(() => setReplay(r => r + 1), 3400);
    return () => clearInterval(id);
  }, []);
  return html`<div class="landing-steps">
    ${STEPS.map(([num, title, body, cast], s) => html`<div class="step-card" key=${num}>
      <div class="step-cast">${cast.map(([mood, focus], i) => html`<${Creature} key=${i} look=${look(s * 3 + i, focus)} mood=${mood} size=${i === 0 ? 80 : 60} replay=${replay} poke />`)}</div>
      <span class="step-num">${num}</span><h3>${title}</h3><p>${body}</p>
    </div>`)}
  </div>`;
}

function Section({id, label, title, lead, children, class: className = ''}) {
  const ref = useReveal();
  return html`<section id=${id} class=${'landing-section reveal ' + className} ref=${ref}>
    ${label ? html`<div class="landing-label">${label}</div>` : null}
    ${title ? html`<h2>${title}</h2>` : null}
    ${lead ? html`<p class="landing-lead">${lead}</p>` : null}
    ${children}
  </section>`;
}

export function Landing({action, repeatable, lead, notice, fine}) {
  const backToStart = html`<button type="button" class="btn btn-lg" onClick=${() => jumpTo('get-started')}>Sign in to start</button>`;
  return html`<div class="landing">
    <nav class="landing-nav">
      <span class="brand"><${BrandMark} />GovernTrade<small>AI</small></span>
      <div class="landing-links">
        <button type="button" onClick=${() => jumpTo('crew')}>Agents</button>
        <button type="button" onClick=${() => jumpTo('how')}>How it works</button>
      </div>
      ${repeatable ? action('sm') : html`<button type="button" class="btn btn-sm" onClick=${() => jumpTo('get-started')}>Sign in</button>`}
    </nav>

    <header class="landing-hero">
      <div class="landing-intro" id="get-started">
        <span class="landing-pill"><i></i>Kalshi demo and live accounts</span>
        <${Headline} />
        <p class="landing-hero-lead">${lead}</p>
        <div class="landing-cta">
          ${action('lg')}
          <button type="button" class="btn btn-lg" onClick=${() => jumpTo('how')}>See how it works</button>
        </div>
        ${notice}
        <p class="landing-fine">${fine}</p>
      </div>
      <${Stage} />
    </header>

    <${Section} id="crew" label="Meet the crew" title="Every agent gets a face, a focus and a mood."
      lead="Give each agent a market to watch. You can tell at a glance who is thinking, who is waiting on you and who just got told no.">
      <${Roster} />
    </${Section}>

    <${Section} id="how" label="How it works" title="Agents propose. The governor decides. You stay in charge.">
      <${Steps} />
    </${Section}>

    <${Section} class="landing-closer-wrap">
      <div class="landing-closer">
        <h2>Your crew is waiting.</h2>
        <p>Connect a Kalshi demo account and watch your first agent pitch a trade.</p>
        ${repeatable ? action('lg') : backToStart}
        <div class="closer-peek" aria-hidden="true">
          ${SEEDS.map((seed, i) => html`<${Creature} key=${seed} look=${{seed, focus: ROSTER[i % ROSTER.length][0]}} size=${88} />`)}
        </div>
      </div>
    </${Section}>

    <footer class="landing-footer">
      <span>GovernTrade AI is a prototype, not a production-certified financial service. Trading involves risk, including loss of capital.</span>
    </footer>
  </div>`;
}
