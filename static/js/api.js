// Talking to the server: sign-in, the workspace state and actions. A port of the previous app.js behaviour:
// errors a person can act on, CSRF on every change, and responses for a previous user are never shown.
import {getStore, setStore, resetStore} from './store.js';

const LOCAL_HOSTS = ['localhost', '127.0.0.1', '[::1]'];
const LOCAL_SESSION_KEY = 'governtrade-session';
let csrf = '';
// Bumped whenever the signed-in user changes. A response that arrives for an older generation is dropped.
let generation = 0;
export const currentGeneration = () => generation;

// fetch and r.json with errors a person can act on: a dropped connection or a proxy's HTML error page
// (e.g. while the server restarts after a deploy) instead of "Failed to fetch" or a JSON parse error.
export async function send(url, options) {
  try {
    return await fetch(url, options);
  } catch {
    throw Error('Could not reach GovernTrade. Check your connection and try again.');
  }
}

export async function readJson(r) {
  try {
    return await r.json();
  } catch {
    throw Error(r.ok ? 'GovernTrade sent an unreadable response. Reload the page.'
      : `GovernTrade is unavailable right now (HTTP ${r.status}). It may be restarting; try again in a minute.`);
  }
}

// Why a hosted sign-in bounced back (?signin=...), in words.
export function signinNotice() {
  const reason = new URLSearchParams(location.search).get('signin');
  return {
    failed: 'Sign-in did not complete. Try again.',
    'not-allowed': 'This account is not on the allowed list for this workspace. Ask the workspace owner to add it.',
  }[reason] || '';
}

function localSession(value) {
  try {
    if (value === undefined) return sessionStorage.getItem(LOCAL_SESSION_KEY) === 'yes';
    if (value) sessionStorage.setItem(LOCAL_SESSION_KEY, 'yes');
    else sessionStorage.removeItem(LOCAL_SESSION_KEY);
  } catch { /* storage blocked: the local entry screen shows each time */ }
  return false;
}

// Forget everything about the current user: their workspace and any screen state built on it.
function resetUser(phase) {
  generation++;
  resetStore({auth: getStore().auth, phase: phase || getStore().phase});
}

export async function initializeAuth() {
  try {
    const r = await send('/api/auth');
    if (r.status === 404 && LOCAL_HOSTS.includes(location.hostname)) {
      setStore({auth: {mode: 'local', user: null, config: null}});
      return enterLocal();
    }
    const d = await readJson(r);
    if (!r.ok) throw Error(d.error || 'Sign-in is unavailable.');
    csrf = d.csrf || '';
    setStore({auth: {mode: d.mode, user: d.user || null, config: d}});
    if ((d.mode === 'google' || d.mode === 'auth0') && !d.user) {
      resetUser('signed-out');
      return;
    }
    if (d.mode === 'local') return enterLocal();
    await loadWorkspace();
  } catch (e) {
    setStore({phase: 'unavailable', error: e.message});
  }
}

function enterLocal() {
  if (!localSession()) {
    setStore({phase: 'local-entry'});
    return;
  }
  return loadWorkspace();
}

// The local workspace entry: this app runs on the user's computer, so there is no password, only a session flag.
export function enterLocalWorkspace() {
  localSession(true);
  return loadWorkspace();
}

export async function loadWorkspace() {
  const g = generation;
  try {
    const r = await send('/api/state?main');
    const d = await readJson(r);
    if (g !== generation) return;
    if (r.status === 401) {
      resetUser('starting');
      return initializeAuth();
    }
    if (!r.ok) throw Error(d.error || 'Unable to load your workspace.');
    csrf = d.csrf;
    setStore({workspace: d.state, phase: 'ready', error: ''});
  } catch (e) {
    if (g !== generation) return;
    if (getStore().workspace) setStore({error: e.message});
    else setStore({phase: 'unavailable', error: e.message});
  }
}

// Run one workspace action. Returns the action's result; throws an Error whose message can be shown as-is.
// Actions are serialised, as before: a second one while the first is saving is refused.
export async function action(name, values = {}) {
  if (getStore().busy) throw Error('The last change is still saving. Try again in a moment.');
  const g = generation;
  setStore({busy: true});
  try {
    const r = await send('/api/action', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
      body: JSON.stringify({action: name, env: 'main', ...values}),
    });
    const d = await readJson(r);
    if (g !== generation) return undefined;
    if (r.status === 401) {
      resetUser('starting');
      await initializeAuth();
      throw Error('Your session expired. Sign in again.');
    }
    if (!r.ok) throw Error(d.error || 'That did not work. Try again.');
    csrf = d.csrf;
    setStore({workspace: d.state});
    return d.result;
  } finally {
    if (g === generation) setStore({busy: false});
  }
}

// Google Identity Services calls this with the signed credential.
export async function googleCredential(response) {
  const nonce = getStore().auth.config?.nonce;
  try {
    const r = await send('/api/auth/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({credential: response.credential, nonce}),
    });
    const d = await readJson(r);
    if (!r.ok) throw Error(d.error || 'Sign-in failed. Try again.');
    csrf = d.csrf;
    setStore({auth: {...getStore().auth, user: d.user}});
    resetUser('starting');
    await loadWorkspace();
  } catch (e) {
    await initializeAuth();
    setStore({error: e.message});
  }
}

export async function signOut() {
  const {mode} = getStore().auth;
  if (mode !== 'google' && mode !== 'auth0') {
    localSession(false);
    resetUser('local-entry');
    return;
  }
  const r = await send('/api/auth/logout', {method: 'POST', headers: {'X-CSRF-Token': csrf}});
  if (!r.ok && r.status !== 401) throw Error('Unable to sign out. Try again.');
  const d = await r.json().catch(() => ({}));
  if (d.logoutUrl) {
    location.href = d.logoutUrl;
    return;
  }
  window.google?.accounts?.id?.disableAutoSelect();
  csrf = '';
  setStore({auth: {...getStore().auth, user: null}});
  resetUser('starting');
  await initializeAuth();
}

// Background refresh never replaces a newer action response or an older user's session.
export async function pollWorkspace() {
  const g = generation, before = getStore().workspace;
  if (getStore().busy || getStore().phase !== 'ready') return;
  try {
    const r = await send('/api/state?main');
    if (g !== generation) return;
    if (r.status === 401) { resetUser('starting'); return initializeAuth(); }
    const d = await readJson(r);
    if (g !== generation || getStore().busy || getStore().workspace !== before || document.visibilityState === 'hidden') return;
    if (!r.ok) throw Error(d.error || 'Unable to refresh your workspace.');
    csrf = d.csrf;
    if (JSON.stringify(before) !== JSON.stringify(d.state)) setStore({workspace: d.state});
  } catch (e) {
    if (g === generation && getStore().phase === 'ready') setStore({error: e.message});
  }
}

// Chat can take a full model turn. Keep its pending state across route changes.
export async function sendChat(agent, text) {
  if (getStore().chatPending) throw Error('Your agent is still working on the last message.');
  const g = generation;
  setStore({chatPending: {agent, text}, error: ''});
  try {
    const r = await send('/api/action', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:JSON.stringify({action:'chat',env:'main',agent,text})});
    const d = await readJson(r);
    if (g !== generation) return;
    if (r.status === 401) {resetUser('starting'); await initializeAuth(); return;}
    // A 4xx is the server refusing the message before it was stored (quota, a turn still running): nothing to check.
    if (r.status >= 400 && r.status < 500) throw Object.assign(Error(d.error || 'Your agent could not respond.'), {refused: true});
    if (!r.ok) throw Error(d.error || 'Your agent could not respond.');
    csrf = d.csrf; setStore({workspace:d.state});
  } catch (e) {
    if (g !== generation) return;
    if (e.refused) throw e;
    await loadWorkspace();
    if (g !== generation) return;
    throw Error(`${e.message} Check the conversation before sending again.`);
  } finally {if (g === generation) setStore({chatPending:null});}
}
