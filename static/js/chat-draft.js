// The message box for one agent, kept in this tab's sessionStorage per user, so a message survives a reload, an
// expired session and signing back in. A message being sent keeps the request ID it was sent with; sending the same
// text again reuses that ID, so the server recognises a retry after a lost response instead of running it twice.
// A message that left the box but is not confirmed is `held` apart from the box, so typing the next message while it
// is being sent can never overwrite it.
const KEY = 'governtrade-chat-draft';
const EMPTY = {text: '', requestId: null, sentText: null, held: null};

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

// {text, requestId, sentText, held}: requestId and sentText are set once a send of the box's sentText was attempted
// and not confirmed; held is {text, requestId}, a message sent from the box and not yet confirmed.
export function loadDraft(owner, agent) {
  const draft = {...EMPTY, ...readAll()[slot(owner, agent)]};
  // The page was left mid-send: an empty box takes the message back, ready to resend with its ID.
  return draft.held && !draft.text ? restoreSend(draft) : draft;
}

export function saveDraft(owner, agent, draft) {
  const all = readAll();
  if (draft.text || draft.requestId || draft.held) all[slot(owner, agent)] = {...EMPTY, ...draft};
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

// Sending `text` with `requestId`: it is held while the box is free for the next message. A held message resent from
// its notice leaves the box as it is.
export function startSend(draft, text, requestId, fromBox = true) {
  const box = fromBox ? {text: '', requestId: null, sentText: null} : {};
  return {...draft, ...box, held: {text, requestId}};
}

// Confirmed: only what was typed since is left.
export const settleSend = draft => ({...draft, held: null});

// Not confirmed: back into an empty box with its request ID. With a new message in the box, it stays held, shown
// above the box to send again or discard.
export function restoreSend(draft) {
  if (!draft.held || draft.text.trim()) return draft;
  return {text: draft.held.text, requestId: draft.held.requestId, sentText: draft.held.text, held: null};
}

// Who a draft belongs to, so it is never shown to a different user signed in later in the same tab.
export const draftOwner = auth => `${auth?.mode || 'local'}:${auth?.user?.email || ''}`;
