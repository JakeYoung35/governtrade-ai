// Creature behaviour shared by every creature on the page: eyes that follow the pointer, a wiggle on click,
// and replaying one-shot moods. All of it stops under reduced motion.

const reducedQuery = matchMedia('(prefers-reduced-motion: reduce)');
const finePointer = matchMedia('(hover: hover) and (pointer: fine)');
export const reducedMotion = () => reducedQuery.matches;

// Only calm, open-eyed moods look at you; the others have their own gaze.
const TRACKING_MOODS = new Set(['idle', 'waiting']);

let frame = 0, lastEvent = null;
function followPointer() {
  frame = 0;
  const e = lastEvent;
  document.querySelectorAll('svg.cr[data-track]').forEach(svg => {
    if (!TRACKING_MOODS.has(svg.dataset.mood)) return;
    const r = svg.getBoundingClientRect();
    if (!r.width) return;
    const dx = e.clientX - (r.left + r.width / 2), dy = e.clientY - (r.top + r.height * .45);
    const d = Math.hypot(dx, dy) || 1, k = Math.min(1, d / 220);
    const face = svg.querySelector('.face');
    face.style.setProperty('--lx', (dx / d * k).toFixed(3));
    face.style.setProperty('--ly', (dy / d * k).toFixed(3));
  });
}

addEventListener('pointermove', e => {
  if (!finePointer.matches || reducedMotion() || e.pointerType !== 'mouse') return;
  lastEvent = e;
  if (!frame) frame = requestAnimationFrame(followPointer);
}, {passive: true});

export function wiggle(svg) {
  if (!svg || reducedMotion()) return;
  svg.querySelector('.wig')?.animate([
    {transform: 'rotate(0)'}, {transform: 'rotate(-9deg) scale(1.03,.97)'}, {transform: 'rotate(7deg)'},
    {transform: 'rotate(-4deg)'}, {transform: 'rotate(2deg)'}, {transform: 'rotate(0)'},
  ], {duration: 540, easing: 'cubic-bezier(.2,.8,.2,1)'});
}

document.addEventListener('click', e => {
  const svg = e.target.closest?.('svg.cr[data-poke]');
  if (svg) wiggle(svg);
});

// Restart a one-shot mood animation (proposed, filled, trimmed).
export function play(svg) {
  if (!svg || reducedMotion()) return;
  svg.classList.remove('play');
  void svg.getBoundingClientRect();
  svg.classList.add('play');
}
