// <Creature look={agent.look} mood="waiting" size={64} />. Renders the generated SVG and plays one-shot moods
// when the mood changes to one of them (not on first render, so a page load never replays old events).
import {useEffect, useMemo, useRef} from 'preact/hooks';
import {html} from '../html.js';
import {creatureSvg, ONE_SHOT} from './generate.js';
import {play} from './motion.js';

export function Creature({look, mood = 'idle', size = 48, label, track = false, poke = false, live = false, replay = 0, class: className = ''}) {
  const box = useRef(null);
  const previous = useRef({mood, replay});
  const markup = useMemo(
    () => creatureSvg(look.seed, {mood, focus: look.focus, size, track, poke, live, label}),
    [look.seed, look.focus, mood, size, track, poke, live, label],
  );
  useEffect(() => {
    const changed = previous.current.mood !== mood || previous.current.replay !== replay;
    previous.current = {mood, replay};
    if (changed && ONE_SHOT.has(mood)) play(box.current?.firstElementChild);
  }, [markup, replay]);
  return html`<span class=${'creature ' + className} style=${`width:${size}px;height:${size}px`} ref=${box} dangerouslySetInnerHTML=${{__html: markup}}></span>`;
}
