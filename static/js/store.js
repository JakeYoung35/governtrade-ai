// One app-wide store. Components read it with useStore(); everything else changes it with setStore().
import {useEffect, useReducer} from 'preact/hooks';
import {moodEvents, hasInFlight} from './order-state.js';

const initial = {
  phase: 'starting',     // starting | signed-out | local-entry | unavailable | ready
  auth: {mode: 'local', user: null, config: null},
  workspace: null,       // the public workspace state from /api/state (accounts, agents, rules, a window of orders...)
  error: '',             // the latest workspace-level error, shown by the screen that caused it
  errorSource: '',       // who set error: poll | action | auth | load. Only a poll's error clears on a later good poll
  moodEvents: {},
  toast: '',
  chatPending: null,
  busy: false,           // an /api/action call is in flight; actions are serialised like before
};
let snapshot = initial;
const listeners = new Set();

export const getStore = () => snapshot;

export function setStore(patch) {
  const next = typeof patch === 'function' ? patch(snapshot) : patch;
  if (next.workspace && next.workspace !== snapshot.workspace) {
    const events = moodEvents(snapshot.workspace, next.workspace);
    next.moodEvents = {...snapshot.moodEvents};
    for (const [agent, mood] of Object.entries(events)) next.moodEvents[agent] = {mood, replay: (snapshot.moodEvents[agent]?.replay || 0) + 1, until: Date.now() + 1800};
  }
  snapshot = {...snapshot, ...next};
  listeners.forEach(listener => listener());
}

export function resetStore(keep = {}) {
  snapshot = {...initial, ...keep};
  listeners.forEach(listener => listener());
}

export function useStore() {
  const [, rerender] = useReducer(n => n + 1, 0);
  const seen = snapshot;
  useEffect(() => {
    listeners.add(rerender);
    // A change between this render and the subscription (effects run after paint) would otherwise be missed.
    if (snapshot !== seen) rerender();
    return () => listeners.delete(rerender);
  }, []);
  return snapshot;
}

// One timer per mounted app; visibility and auth changes invalidate pending reads.
export function startPolling(poll) {
  let timer, stopped = false, running = false;
  const schedule = () => {
    clearTimeout(timer);
    if (!stopped && document.visibilityState !== 'hidden' && snapshot.phase === 'ready')
      timer = setTimeout(tick, hasInFlight(snapshot.workspace) ? 3000 : 15000);
  };
  const tick = async () => {
    if (running) return;
    running = true;
    try {
      if (!stopped && document.visibilityState !== 'hidden' && snapshot.phase === 'ready' && !snapshot.busy) await poll();
    } finally { running = false; schedule(); }
  };
  const changed = () => schedule();
  listeners.add(changed);
  document.addEventListener('visibilitychange', changed);
  schedule();
  return () => { stopped = true; clearTimeout(timer); listeners.delete(changed); document.removeEventListener('visibilitychange', changed); };
}
