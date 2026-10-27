// An agent's mood for a creature on screen: a one-shot event (proposed, trimmed, filled) while it plays, otherwise
// the persistent mood from moodFor. The store records events as workspace updates arrive (order-state.moodEvents).
import {useEffect, useState} from 'preact/hooks';
import {useStore} from '../store.js';
import {moodFor} from './moods.js';

export function useAgentMood(agent, workspace, {thinking = false} = {}) {
  const event = useStore().moodEvents[agent.id];
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    setNow(Date.now());
    const timer = setTimeout(() => setNow(Date.now()), Math.max(0, (event?.until || 0) - Date.now()));
    return () => clearTimeout(timer);
  }, [event]);
  const persistent = moodFor(agent, workspace, {thinking});
  return {mood: event?.until > now ? event.mood : persistent, persistent, replay: event?.replay || 0};
}
