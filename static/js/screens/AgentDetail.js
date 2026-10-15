// One agent: its creature and status, the conversation with governed proposals, and its mandate. From here the
// user pauses or resumes it, edits its model, changes its look, edits its mandate or deletes it.
import {useEffect, useLayoutEffect, useRef, useState} from 'preact/hooks';
import {html} from '../html.js';
import {Icon} from '../ui/icons.js';
import {action, sendChat} from '../api.js';
import {useStore, getStore, setStore} from '../store.js';
import {navigate} from '../router.js';
import {money, accountValue} from '../format.js';
import {reasonText, statusOf} from '../governor-text.js';
import {thread, wasDelivered, governorLine, tradingBlocked} from '../chat.js';
import {loadDraft, saveDraft, requestIdFor, draftOwner} from '../chat-draft.js';
import {Creature} from '../creature/Creature.js';
import {FOCI, colorOf, freshSeed, lookFor} from '../creature/generate.js';
import {useAgentMood} from '../creature/useAgentMood.js';
import {accountLabel, providerName, takenColors} from './Agents.js';
import {MandateStep} from './NewAgent.js';
import {Dialog} from '../ui/Dialog.js';
import {ApprovalCard, orderHref} from '../ui/ApprovalCard.js';

const STATUS = {
  active: ['Active', 'pill-allow'],
  paused: ['Paused', 'pill-neutral'],
  draft: ['Needs a mandate', 'pill-review'],
};
const EXAMPLE = 'Put $10 on the Eagles to win their next game.';

function ErrorNote({message}) {
  return message ? html`<div class="notice notice-error" role="alert"><${Icon} name="alert" /><span>${message}</span></div>` : null;
}

// Free agents share a daily message allowance, counted by the server for its own UTC day (Workspace.public's
// freeAgentQuota), so the browser's clock never decides it. Null for an agent on the user's own model.
export function freeMessagesLeft(workspace, agent) {
  const quota = workspace.freeAgentQuota;
  if (!workspace.providers?.[agent.provider]?.free || !quota) return null;
  return {left: quota.left, total: quota.limit};
}

function Hero({agent, workspace, account, thinking, onOpen, onError}) {
  const {busy} = useStore();
  const {mood, replay} = useAgentMood(agent, workspace, {thinking});
  const [statusLabel, statusClass] = STATUS[agent.status] || [agent.status, 'pill-neutral'];
  const look = lookFor(agent);
  const focus = FOCI.find(f => f.id === look.focus)?.label;
  const free = freeMessagesLeft(workspace, agent);

  async function setStatus(status) {
    onError('');
    try { await action('agent-status', {id: agent.id, status}); } catch (e) { onError(e.message); }
  }

  return html`<section class="card agent-hero">
    <div class="agent-hero-art"><${Creature} look=${look} mood=${mood} replay=${replay} size=${76} poke live=${account?.environment === 'live'} /></div>
    <div class="agent-hero-body">
      <div class="agent-card-title"><h2>${agent.name}</h2><span class=${'pill ' + statusClass}>${statusLabel}</span></div>
      <p>${focus} · ${providerName(workspace, agent)} · ${agent.model}</p>
      <p class="muted">${accountLabel(account)}${free ? ` · ${free.left} of ${free.total} free messages left today` : agent.modelKeyMask ? ` · key ${agent.modelKeyMask}` : ''}</p>
    </div>
    <div class="agent-hero-actions">
      ${agent.status === 'active' ? html`<button class="btn" disabled=${busy} onClick=${() => setStatus('paused')}>Pause</button>` : null}
      ${agent.status === 'paused' ? html`<button class="btn btn-primary" disabled=${busy} onClick=${() => setStatus('active')}>Resume</button>` : null}
      ${agent.status === 'draft' ? html`<button class="btn btn-primary" onClick=${() => onOpen('mandate')}>Set mandate</button>` : null}
      <button class="btn" onClick=${() => onOpen('edit')}>Edit agent</button>
      <button class="btn" onClick=${() => onOpen('look')}>Change look</button>
      <button class="btn btn-ghost btn-danger-text" onClick=${() => onOpen('delete')}>Delete</button>
    </div>
  </section>`;
}

// The first check that blocked an order in its latest evaluation, e.g. "Agent mandate: Trade exceeds the agent daily limit."
function blockReason(order) {
  const latest = order.evaluations?.[order.evaluations.length - 1];
  const check = latest?.checks?.find(c => c.outcome === 'BLOCK');
  return check ? `${check.name}: ${check.reason}` : null;
}

function ProposalCard({proposal, workspace}) {
  const order = workspace.orders?.find(o => o.id === proposal.order?.id);
  if (order?.status === 'awaiting approval') return html`<${ApprovalCard} order=${order} workspace=${workspace} />`;
  const {label, tone} = statusOf(order ? order.status : proposal.status);
  const estimate = Number(proposal.estimatedYesProbability);
  const outcome = governorLine(proposal, {money, reasonText});
  const blocked = order?.decision === 'BLOCK' ? blockReason(order) : null;
  return html`<article class="proposal">
    <div class="proposal-head"><h3>${proposal.marketName || order?.market?.name || proposal.marketId}</h3><span class=${`pill pill-${tone}`}>${label}</span></div>
    <p>Buy ${String(proposal.side || '').toUpperCase()} · up to ${money(proposal.maxBudgetUsd)}${Number.isFinite(estimate) ? ` · agent estimate ${Math.round(estimate * 100)}% YES` : ''}</p>
    ${outcome ? html`<p>${outcome}</p>` : proposal.status === 'queued' ? html`<p class="muted">Waiting for the next governor batch.</p>` : null}
    ${blocked ? html`<p class="proposal-block">${blocked}</p>` : null}
    ${proposal.note ? html`<p class="muted">${proposal.note}</p>` : null}
    ${order ? html`<a class="proposal-link" href=${orderHref(order.id)}>Review order and checks</a>` : null}
  </article>`;
}

function Message({item, agent, workspace}) {
  const {message, proposals} = item;
  const cards = proposals.length ? html`<div class="msg-proposals">${proposals.map(p => html`<${ProposalCard} key=${p.governorProposalId} proposal=${p} workspace=${workspace} />`)}</div>` : null;
  if (message.role === 'customer') return html`<div class="msg msg-you"><span class="visually-hidden">You said</span><p class="bubble">${message.text}</p>${cards}</div>`;
  return html`<div class="msg msg-agent">
    <${Creature} look=${lookFor(agent)} size=${28} class="msg-avatar" />
    <div class="msg-main"><span class="msg-name">${agent.name}</span><p class="bubble">${message.text}</p>${cards}</div>
  </div>`;
}

function Conversation({agent, workspace}) {
  const store = useStore();
  const owner = draftOwner(store.auth);
  // The box's text and, while a send is unconfirmed, its request ID (chat-draft.js). Kept in sessionStorage so a
  // reload or an expired session never loses a message, and a resend after a lost response never runs it twice.
  const [draft, setDraftState] = useState(() => loadDraft(owner, agent.id));
  const latest = useRef(draft);
  const [interrupted, setInterrupted] = useState(() => !!(draft.requestId && draft.text && draft.text === draft.sentText));
  const [error, setError] = useState('');
  // `stored` differs from what the box shows only while a message is being sent: the box is empty, storage keeps it.
  function update(next, stored = next) {
    latest.current = next;
    setDraftState(next);
    saveDraft(owner, agent.id, stored);
  }
  const setDraft = text => update({...latest.current, text});
  const log = useRef(null);
  const input = useRef(null);
  const messages = workspace.chats?.[agent.id] || [];
  const items = thread(messages, workspace.chatProposals?.[agent.id] || []);
  const pending = store.chatPending?.agent === agent.id ? store.chatPending : null;
  const otherPending = store.chatPending && !pending;
  const free = freeMessagesLeft(workspace, agent);
  const canSend = !store.chatPending && draft.text.trim();
  const blocked = tradingBlocked(workspace, agent, workspace.accounts?.find(a => a.id === agent.accountId));

  // Keep the newest message in view: on open, on each new message, and while the agent is thinking.
  useLayoutEffect(() => { if (log.current) log.current.scrollTop = log.current.scrollHeight; }, [messages.length, !!pending]);

  async function send(e) {
    e?.preventDefault();
    if (!canSend) return;
    const text = draft.text.trim();
    const requestId = requestIdFor(latest.current, text);
    const lengthBefore = messages.length;
    const sending = {text, requestId, sentText: text};
    update({...sending, text: ''}, sending);
    setError('');
    setInterrupted(false);
    // Done with this message: keep only what was typed since, as a new message.
    const settle = () => update(latest.current.text ? {text: latest.current.text, requestId: null, sentText: null} : {text: '', requestId: null, sentText: null});
    // Not confirmed: put it back with its request ID, unless something new was typed meanwhile.
    const restore = () => { if (!latest.current.text.trim()) update(sending); };
    try {
      const result = await sendChat(agent.id, text, requestId);
      settle();
      if (result?.duplicate) setStore({toast: `${agent.name} already had that message, so it was not sent twice.`});
    } catch (err) {
      if (err.lost && wasDelivered(getStore().workspace?.chats?.[agent.id], lengthBefore, text)) {
        settle();  // the response was lost, but the reload shows the message arrived
        setStore({toast: `The connection dropped, but ${agent.name} got your message.`});
      } else {
        setError(err.message);
        restore();
      }
    }
    input.current?.focus();
  }

  // Enter sends on a keyboard; Shift+Enter adds a line. On touch screens the Send button sends.
  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && matchMedia('(pointer:fine)').matches) send(e);
  }

  return html`<section class="card chat" aria-label="Conversation">
    <div class="card-head"><h2>Conversation</h2>${free ? html`<span class="muted chat-quota">${free.left} of ${free.total} free messages left today</span>` : null}</div>
    <div class="chat-log" ref=${log} role="log" aria-live="polite" aria-label=${`Conversation with ${agent.name}`} tabindex="0">
      ${!items.length && !pending ? html`<div class="chat-empty">
        <${Creature} look=${lookFor(agent)} size=${56} track />
        <p>Tell ${agent.name} what to trade. It finds the Kalshi market and sends a proposal to the governor, which sizes it. Your mandate decides what runs automatically.</p>
        <button type="button" class="btn btn-sm" onClick=${() => { setDraft(EXAMPLE); input.current?.focus(); }}>Try “${EXAMPLE}”</button>
      </div>` : null}
      ${items.map(item => html`<${Message} key=${item.index} item=${item} agent=${agent} workspace=${workspace} />`)}
      ${pending ? html`<div class="msg msg-you"><p class="bubble">${pending.text}</p></div>
        <div class="msg msg-agent" role="status">
          <${Creature} look=${lookFor(agent)} size=${28} mood="thinking" class="msg-avatar" />
          <div class="msg-main"><span class="msg-name">${agent.name}</span><p class="bubble bubble-thinking">Reading your message and checking Kalshi markets<span class="dots" aria-hidden="true"><i></i><i></i><i></i></span></p></div>
        </div>` : null}
    </div>
    <form class="chat-composer" onSubmit=${send}>
      <${ErrorNote} message=${error} />
      ${interrupted && !error ? html`<div class="notice notice-warn"><${Icon} name="info" /><span>This message was being sent when the page was interrupted. Send it again: if ${agent.name} already has it, it will not run twice.</span></div>` : null}
      ${blocked ? html`<div class="notice"><${Icon} name="info" /><span>${agent.name} can answer questions but will not propose trades. ${blocked}</span></div>` : null}
      <div class="chat-input">
        <label class="visually-hidden" for="chat-text">Message ${agent.name}</label>
        <textarea id="chat-text" ref=${input} class="input" rows="2" maxlength="2000" value=${draft.text} placeholder=${EXAMPLE}
          onInput=${e => setDraft(e.currentTarget.value)} onKeyDown=${onKeyDown}></textarea>
        <button class="btn btn-primary" disabled=${!canSend}>${pending ? html`<span class="spinner" aria-hidden="true"></span>Working` : 'Send'}</button>
      </div>
      <p class="fine-print">${otherPending ? 'Another agent is still answering. Send this when it is done.' : 'The governor sizes every proposal. Trades above the automatic limit wait for your approval.'}</p>
    </form>
  </section>`;
}

function MandateCard({agent, workspace, account, onEdit}) {
  const fields = workspace.rulesCatalog?.mandateFields || [];
  const value = accountValue(account?.balance);
  const m = agent.mandate;
  const dollars = key => value != null ? money(m[key] / 100 * value) : null;
  const rows = m ? fields.map(f => {
    if (f.unit === 'boolean') return [f.label, m[f.key] ? 'Yes, the first trade waits for you' : 'No', null, true];
    if (f.key === 'dailyCapPercent') return [f.label, `${m[f.key]}%`, `${money(agent.usage?.daily || 0)} spent of ${dollars(f.key) || `${m[f.key]}%`} today`];
    return [f.label, `${m[f.key]}%`, dollars(f.key)];
  }) : [];
  return html`<section class="card">
    <div class="card-head"><h2>Mandate</h2><button class="btn btn-sm" onClick=${onEdit}>${m ? 'Edit' : 'Set mandate'}</button></div>
    <div class="card-body">
      ${m ? html`<dl class="limit-list">${rows.map(([k, v, note, words]) => html`<div><dt>${k}</dt><dd><strong class=${words ? null : 'num'}>${v}</strong>${note ? html`<small>${note}</small>` : null}</dd></div>`)}</dl>
        <p class="fine-print">Shares of this account's value, on top of your trading rules. Only you can change them.</p>`
      : html`<p>${agent.name} needs a mandate before it can trade.</p>`}
    </div>
  </section>`;
}

function EditAgent({agent, workspace, close, onSaved}) {
  const providers = workspace.providers || {};
  const [draft, setDraft] = useState({name: agent.name, provider: agent.provider, model: agent.model, modelKey: ''});
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const set = patch => setDraft(d => ({...d, ...patch}));
  const free = providers[draft.provider]?.free;
  const keyNeeded = !free && (draft.provider !== agent.provider || !agent.modelKeyMask);
  // A model the provider no longer lists stays selectable on its own agent, so the form never shows a blank model.
  const offered = providers[draft.provider]?.models || [];
  const retired = draft.provider === agent.provider && !offered.includes(agent.model);
  const models = retired ? [agent.model, ...offered] : offered;
  const valid = draft.name.trim() && draft.model && (!keyNeeded || draft.modelKey.trim());

  async function save(e) {
    e.preventDefault();
    if (!valid) return;
    setError('');
    setSaving(true);
    try {
      await action('agent-save', {id: agent.id, accountId: agent.accountId, name: draft.name.trim(), provider: draft.provider, model: draft.model,
        ...(free || !draft.modelKey.trim() ? {} : {modelKey: draft.modelKey.trim()})});
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return html`<form class="dialog-form" onSubmit=${save}>
    <label class="field"><span class="field-label">Name</span>
      <input class="input" required maxlength="100" value=${draft.name} onInput=${e => set({name: e.currentTarget.value})} /></label>
    <div class="field-row">
      <label class="field"><span class="field-label">Provider</span>
        <select class="input select" value=${draft.provider} onChange=${e => { const id = e.currentTarget.value; set({provider: id, model: providers[id]?.models[0] || '', modelKey: ''}); }}>
          ${Object.entries(providers).map(([id, p]) => html`<option value=${id}>${p.label}</option>`)}
        </select></label>
      <label class="field"><span class="field-label">Model</span>
        <select class="input select" value=${draft.model} onChange=${e => set({model: e.currentTarget.value})}>
          ${models.map(m => html`<option value=${m}>${m}${m === agent.model && retired ? ' (no longer offered)' : ''}</option>`)}
        </select></label>
    </div>
    ${free ? html`<p class="fine-print">${providers[draft.provider].notice}</p>` : html`<label class="field"><span class="field-label">API key</span>
      <input class="input" type="password" autocomplete="new-password" spellcheck="false" value=${draft.modelKey} placeholder=${keyNeeded ? '' : `Saved key ${agent.modelKeyMask}`} onInput=${e => set({modelKey: e.currentTarget.value})} />
      <span>${keyNeeded ? 'This provider needs its own key.' : 'Leave blank to keep the saved key.'} Saving checks the key and model with the provider.</span></label>`}
    <p class="fine-print">${agent.name} stays on ${accountLabel(workspace.accounts?.find(a => a.id === agent.accountId))}. Create a new agent to trade on another account.</p>
    <${ErrorNote} message=${error} />
    <div class="dialog-actions">
      <button type="button" class="btn btn-ghost" onClick=${close}>Cancel</button>
      <button class="btn btn-primary" disabled=${!valid || saving}>${saving ? html`<span class="spinner" aria-hidden="true"></span>Checking the model` : 'Save'}</button>
    </div>
  </form>`;
}

function ChangeLook({agent, workspace, close, onSaved}) {
  const [look, setLook] = useState(() => lookFor(agent));
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const taken = takenColors(workspace, agent.id);

  async function save(e) {
    e.preventDefault();
    setError('');
    setSaving(true);
    try {
      await action('agent-look', {id: agent.id, look: {seed: look.seed, focus: look.focus}});
      onSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return html`<form class="dialog-form" onSubmit=${save}>
    <div class="look-preview">
      <${Creature} look=${look} size=${120} track poke label=${agent.name} />
      <button type="button" class="btn" onClick=${() => setLook({...look, seed: freshSeed(taken)})}>Reroll</button>
      <span class="muted look-caption">${colorOf(look).name}. This colour marks ${agent.name} everywhere in the app.</span>
    </div>
    <fieldset class="field">
      <legend class="field-label">Markets it focuses on</legend>
      <span>This picks its outfit. It does not limit what it can trade.</span>
      <div class="chips">${FOCI.map(f => html`<label class="chip"><input type="radio" name="look-focus" value=${f.id} checked=${look.focus === f.id} onChange=${() => setLook({...look, focus: f.id})} /><span>${f.label}</span></label>`)}</div>
    </fieldset>
    <${ErrorNote} message=${error} />
    <div class="dialog-actions">
      <button type="button" class="btn btn-ghost" onClick=${close}>Cancel</button>
      <button class="btn btn-primary" disabled=${saving}>Save look</button>
    </div>
  </form>`;
}

function DeleteAgent({agent, close}) {
  const [error, setError] = useState('');
  const {busy} = useStore();
  async function remove() {
    setError('');
    try {
      await action('agent-delete', {id: agent.id});
      setStore({toast: `${agent.name} deleted.`});
      navigate('/agents', {replace: true});
    } catch (err) {
      setError(err.message);
    }
  }
  return html`<div class="dialog-form">
    <p>${agent.name} stops trading and its saved model key is removed. Its orders and decisions stay in your activity history. This cannot be undone.</p>
    <${ErrorNote} message=${error} />
    <div class="dialog-actions">
      <button type="button" class="btn btn-ghost" onClick=${close}>Cancel</button>
      <button type="button" class="btn btn-danger" disabled=${busy} onClick=${remove}>Delete ${agent.name}</button>
    </div>
  </div>`;
}

const DIALOGS = {edit: 'Edit agent', look: 'Change look', mandate: 'Mandate'};

export function AgentDetail({id, workspace}) {
  const store = useStore();
  const [modal, setModal] = useState('');
  const [error, setError] = useState('');
  const agent = workspace.agentConfigs?.find(a => a.id === id && a.status !== 'deleted');
  useEffect(() => setError(''), [agent?.status]);

  if (!agent) {
    return html`<div class="card"><div class="empty"><h3>Agent not found</h3><p>It may have been deleted.</p><a class="btn" href="/agents">Back to agents</a></div></div>`;
  }
  const account = workspace.accounts?.find(a => a.id === agent.accountId);
  const inChat = new Set((workspace.chatProposals?.[id] || []).map(p => p.order?.id).filter(Boolean));
  const waiting = (workspace.orders || []).filter(o => o.agent === id && o.status === 'awaiting approval' && !inChat.has(o.id));
  const close = () => setModal('');
  const saved = message => () => { setModal(''); setStore({toast: message}); };

  return html`<div class="section-stack">
    <a class="back-link" href="/agents"><span aria-hidden="true">←</span>All agents</a>
    <${Hero} agent=${agent} workspace=${workspace} account=${account} thinking=${store.chatPending?.agent === id} onOpen=${setModal} onError=${setError} />
    <${ErrorNote} message=${error} />
    <div class="agent-layout">
      <div class="section-stack">
        ${waiting.length ? html`<a class="notice notice-warn notice-link" href="/approvals">
          <${Icon} name="approvals" /><span>${waiting.length === 1 ? `1 order from ${agent.name} is` : `${waiting.length} orders from ${agent.name} are`} waiting for your approval.</span><span class="notice-action">Review<${Icon} name="arrow" /></span>
        </a>` : null}
        <${Conversation} agent=${agent} workspace=${workspace} />
      </div>
      <aside class="section-stack"><${MandateCard} agent=${agent} workspace=${workspace} account=${account} onEdit=${() => setModal('mandate')} /></aside>
    </div>
    ${modal ? html`<${Dialog} title=${modal === 'delete' ? `Delete ${agent.name}?` : DIALOGS[modal]} onClose=${close}>
      ${modal === 'edit' ? html`<${EditAgent} agent=${agent} workspace=${workspace} close=${close} onSaved=${saved('Agent saved.')} />` : null}
      ${modal === 'look' ? html`<${ChangeLook} agent=${agent} workspace=${workspace} close=${close} onSaved=${saved('Look saved.')} />` : null}
      ${modal === 'mandate' ? html`<${MandateStep} agent=${agent} workspace=${workspace} onSaved=${saved('Mandate saved. New proposals use it.')} onCancel=${close} />` : null}
      ${modal === 'delete' ? html`<${DeleteAgent} agent=${agent} close=${close} />` : null}
    </${Dialog}>` : null}
  </div>`;
}
