// The message box for one agent, kept in this tab's sessionStorage per user, so a message survives a reload, an
// expired session and signing back in. A message being sent keeps the request ID it was sent with; sending the same
// text again reuses that ID, so the server recognises a retry after a lost response instead of running it twice.
const KEY = 'governtrade-chat-draft';
const EMPTY = {text: '', requestId: null, sentText: null};

function readAll() {
  try { return JSON.parse(sessionStorage.getItem(KEY)) || {}; } catch { return {}; }
}

function writeAll(all) {
  try {
    if (Object.keys(all).length) sessionStorage.setItem(KEY, JSON.stringify(all));
    else sessionStorage.removeItem(KEY);
  } catch { /* storage blocked: the draft lives only in the page */ }
}

const slot = (owner, agent) => `${owner}\n${agent}`;

// {text, requestId, sentText}: requestId and sentText are set once a send of sentText was attempted and not confirmed.
export function loadDraft(owner, agent) {
  return {...EMPTY, ...readAll()[slot(owner, agent)]};
}

export function saveDraft(owner, agent, draft) {
  const all = readAll();
  if (draft.text || draft.requestId) all[slot(owner, agent)] = {...EMPTY, ...draft};
  else delete all[slot(owner, agent)];
  writeAll(all);
}

export function clearDraft(owner, agent) {
  saveDraft(owner, agent, EMPTY);
}

// The ID to send `text` with: the unconfirmed earlier send's ID when the text is unchanged, otherwise a new one.
export function requestIdFor(draft, text, newId = () => crypto.randomUUID()) {
  return draft.requestId && draft.sentText === text ? draft.requestId : newId();
}

// Who a draft belongs to, so it is never shown to a different user signed in later in the same tab.
export const draftOwner = auth => `${auth?.mode || 'local'}:${auth?.user?.email || ''}`;
