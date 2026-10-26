// Path-based routing with the History API. The server answers every page path with the app shell (server.app_route).
import {useEffect, useReducer} from 'preact/hooks';

const listeners = new Set();
const notify = () => listeners.forEach(listener => listener());

export function navigate(to, {replace = false} = {}) {
  if (to === location.pathname + location.search) return;
  const target = new URL(to, location.href);
  const changingPage = target.pathname !== location.pathname;
  const openingOrder = target.searchParams.has('order') && !new URLSearchParams(location.search).has('order');
  history[replace ? 'replaceState' : 'pushState'](openingOrder && !replace ? {orderDrawer: true} : null, '', to);
  if (changingPage) window.scrollTo(0, 0);
  notify();
}

addEventListener('popstate', notify);

// Plain <a href="/agents"> links navigate in the app; new-tab clicks and external links behave normally.
document.addEventListener('click', e => {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const a = e.target.closest('a[href]');
  if (!a || a.target || a.hasAttribute('download')) return;
  const url = new URL(a.href, location.href);
  if (url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  e.preventDefault();
  navigate(url.pathname + url.search);
});

// [pattern, name]: ':id' segments become params.
const ROUTES = [
  ['/', 'home'],
  ['/approvals', 'approvals'],
  ['/agents', 'agents'],
  ['/agents/new', 'new-agent'],
  ['/agents/:id', 'agent'],
  ['/activity', 'activity'],
  ['/rules', 'rules'],
  ['/accounts', 'accounts'],
  ['/demo', 'demo'],
];

export function match(pathname) {
  const parts = pathname.replace(/\/+$/, '').split('/').filter(Boolean);
  // A malformed escape (/agents/%E0%A4%A) is an unknown page, not a crash while rendering.
  const decode = segment => { try { return decodeURIComponent(segment); } catch { return null; } };
  for (const [pattern, name] of ROUTES) {
    const want = pattern.split('/').filter(Boolean);
    if (want.length !== parts.length) continue;
    const params = {};
    if (want.every((seg, i) => seg.startsWith(':') ? (params[seg.slice(1)] = decode(parts[i])) !== null : seg === parts[i])) {
      return {name, params};
    }
  }
  return {name: 'not-found', params: {}};
}

export function useRoute() {
  const [, rerender] = useReducer(n => n + 1, 0);
  const seen = location.pathname + location.search;
  useEffect(() => {
    listeners.add(rerender);
    if (location.pathname + location.search !== seen) rerender();
    return () => listeners.delete(rerender);
  }, []);
  return {...match(location.pathname), query: new URLSearchParams(location.search)};
}
