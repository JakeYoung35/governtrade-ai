// Reading data the workspace state leaves out (order_views.py on the server): pages of orders and events, one full
// order, Activity's counts. The state carries only a bounded window, because a workspace's history grows without limit.
import {useEffect, useState} from 'preact/hooks';
import {fetchData, currentGeneration} from './api.js';

export const PAGE_LIMIT = 100;  // the most rows the server returns per read
const REMEMBERED = 50;          // answers kept, so a screen opened again shows its last answer while it reads again

// The last answer per set of URLs, for the signed-in user only (an answer from before a sign-in change is ignored).
const answers = new Map();
function remembered(key) {
  const answer = answers.get(key);
  return answer && answer.generation === currentGeneration() ? answer.data : null;
}
function remember(key, generation, data) {
  answers.delete(key);
  answers.set(key, {generation, data});
  if (answers.size > REMEMBERED) answers.delete(answers.keys().next().value);
}

// URLs that read the first `wanted` rows of a paged list, at most PAGE_LIMIT per read.
export function pageUrls(path, params, wanted) {
  const urls = [];
  for (let offset = 0; offset < wanted; offset += PAGE_LIMIT) {
    const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value !== '' && value != null));
    query.set('offset', offset);
    query.set('limit', Math.min(PAGE_LIMIT, wanted - offset));
    urls.push(`${path}?${query}`);
  }
  return urls;
}

// Reads `urls` together, again whenever they or `version` change (pass the workspace, so the data follows each
// refresh). While a read runs, the last answer for these URLs stays on screen (or, for URLs never read, the previous
// answer); `data` is null only before any answer. An answer for URLs no longer wanted is dropped. `urls` null reads
// nothing.
export function useReads(urls, version) {
  const key = urls ? urls.join('\n') : '';
  const [state, setState] = useState(() => ({key: '', data: urls ? remembered(key) : null, error: null}));
  useEffect(() => {
    if (!urls) return undefined;
    let live = true;
    const generation = currentGeneration();
    Promise.all(urls.map(fetchData)).then(
      data => {
        if (data.includes(undefined)) return;  // the signed-in user changed meanwhile
        remember(key, generation, data);
        if (live) setState({key, data, error: null});
      },
      error => { if (live) setState(s => ({...s, key, error})); });
    return () => { live = false; };
  }, [key, version]);
  const current = state.key === key;
  return {data: current ? state.data : (urls && remembered(key)) || state.data, error: state.error, loading: !!urls && !current};
}
