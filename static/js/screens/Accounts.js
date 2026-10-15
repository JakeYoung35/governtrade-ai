// Kalshi connections: connect Demo or Live (with a subaccount), test, rotate keys, disconnect and remove, and each
// account's balance and open positions. Funds never leave Kalshi; the keys are trade-only and stored encrypted.
import {useState} from 'preact/hooks';
import {html} from '../html.js';
import {Icon} from '../ui/icons.js';
import {Dialog} from '../ui/Dialog.js';
import {action} from '../api.js';
import {useStore, setStore} from '../store.js';
import {money, when, accountValue} from '../format.js';

const KEY_PAGES = {'kalshi-demo': 'https://demo.kalshi.co/', 'kalshi-live': 'https://kalshi.com/account/profile'};

function ErrorNote({message}) {
  return message ? html`<div class="notice notice-error" role="alert"><${Icon} name="alert" /><span>${message}</span></div>` : null;
}

function connection(account) {
  if (account.status === 'error') return ['Needs attention', 'pill-block'];
  if (account.status === 'disconnected') return [account.hasCredentials ? 'Not tested' : 'Disconnected', 'pill-neutral'];
  if (account.stale) return ['Balance out of date', 'pill-review'];
  return ['Connected', 'pill-allow'];
}

// Kalshi reports a position as a signed contract count: positive holds YES, negative holds NO.
function Positions({positions}) {
  const open = (positions || []).filter(p => Number(p.position_fp) !== 0);
  if (!open.length) return html`<p class="muted account-note">No open positions.</p>`;
  return html`<details class="account-positions"><summary>Open positions (${open.length})</summary>
    <div class="table-scroll"><table class="decisions-table">
      <thead><tr><th scope="col">Market</th><th scope="col">Side</th><th scope="col">Contracts</th><th scope="col">Exposure</th></tr></thead>
      <tbody>${open.map(p => html`<tr key=${p.ticker}><td>${p.ticker}</td><td>${Number(p.position_fp) > 0 ? 'YES' : 'NO'}</td>
        <td class="num">${Math.abs(Number(p.position_fp)).toLocaleString('en-US')}</td><td class="num">${money(p.market_exposure_dollars)}</td></tr>`)}</tbody>
    </table></div>
  </details>`;
}

function AccountCard({account, open}) {
  const {busy} = useStore();
  const [error, setError] = useState('');
  const [testing, setTesting] = useState(false);
  const [label, tone] = connection(account);
  const b = account.balance;
  const keyId = account.credentialMasks?.key_id;

  async function test() {
    setError('');
    setTesting(true);
    try {
      await action('account-test', {id: account.id});
      setStore({toast: `${account.name} is connected.`});
    } catch (err) {
      setError(err.message);
    } finally {
      setTesting(false);
    }
  }

  const figures = [['Account value', accountValue(b)], ['Available cash', b?.cash], ['Reserved', b?.reserved],
    ['Positions', b?.positionValue], ['Realized P/L', b?.realizedPnl], ['Unrealized P/L', b?.unrealizedPnl]];
  return html`<article class="card account-card">
    <div class="account-head">
      <div><h2>${account.name}</h2>
        <p class="muted">Kalshi ${account.environment === 'live' ? 'Live' : 'Demo'} · subaccount ${account.subaccount || 0} · ${account.hasCredentials ? `key ${keyId || 'saved'}` : 'no saved keys'}</p></div>
      <div class="account-pills">${account.environment === 'live' ? html`<span class="pill pill-ink">LIVE MONEY</span>` : html`<span class="pill pill-outline">Demo money</span>`}
        <span class=${'pill ' + tone}>${label}</span></div>
    </div>
    ${account.error ? html`<div class="notice notice-error account-alert"><${Icon} name="alert" /><span>${account.error}</span></div>` : null}
    ${!account.hasCredentials ? html`<div class="notice account-alert"><${Icon} name="info" /><span>The keys were removed. Add new keys under Settings and keys to reconnect, or remove the account.</span></div>` : null}
    ${b ? html`<dl class="account-figures">${figures.map(([k, v]) => html`<div><dt>${k}</dt><dd class="num">${money(v)}</dd></div>`)}</dl>
      <p class="muted account-note">${account.stale ? 'Last known balance' : 'Updated'} ${when(b.asOf)}${b.source ? ` · from ${b.source}` : ''}</p>
      <${Positions} positions=${b.positions} />`
    : html`<p class="muted account-note">No balance yet. Test the connection to read it from Kalshi.</p>`}
    <${ErrorNote} message=${error} />
    <div class="account-actions">
      <button class=${account.status !== 'connected' && account.hasCredentials ? 'btn btn-primary' : 'btn'} disabled=${busy || !account.hasCredentials} onClick=${test}>
        ${testing ? html`<span class="spinner" aria-hidden="true"></span>Testing` : 'Test connection'}</button>
      <button class="btn" onClick=${() => open({type: 'edit', account})}>Settings and keys</button>
      <span class="spacer"></span>
      ${account.hasCredentials ? html`<button class="btn btn-ghost btn-danger-text" onClick=${() => open({type: 'disconnect', account})}>Disconnect</button>`
        : html`<button class="btn btn-ghost btn-danger-text" onClick=${() => open({type: 'remove', account})}>Remove</button>`}
    </div>
  </article>`;
}

function ConnectionForm({account, close}) {
  const editing = !!account;
  const [draft, setDraft] = useState({name: account?.name || '', connectionType: account?.connectionType || 'kalshi-demo',
    subaccount: String(account?.subaccount ?? 0), keyId: '', privateKey: '', confirmLive: false});
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const set = patch => setDraft(d => ({...d, ...patch}));
  const live = draft.connectionType === 'kalshi-live';
  const keysGiven = draft.keyId.trim() || draft.privateKey.trim();
  const keysNeeded = !account?.hasCredentials;
  const sub = Number(draft.subaccount);
  const subValid = Number.isInteger(sub) && sub >= 0 && sub <= 63 && draft.subaccount.trim() !== '';
  const valid = draft.name.trim() && subValid && (!keysNeeded || (draft.keyId.trim() && draft.privateKey.trim()))
    && (!keysGiven || (draft.keyId.trim() && draft.privateKey.trim())) && (!live || draft.confirmLive);

  async function save(e) {
    e.preventDefault();
    if (!valid) return;
    setError('');
    setSaving(true);
    try {
      await action('account-save', {id: account?.id, name: draft.name.trim(), connectionType: draft.connectionType, subaccount: sub,
        confirmLive: live && draft.confirmLive, ...(keysGiven ? {credentials: {key_id: draft.keyId.trim(), private_key: draft.privateKey.trim()}} : {})});
      set({keyId: '', privateKey: ''});  // the keys now live encrypted on the server; do not keep them in the page
      setStore({toast: `${draft.name.trim()} saved. Test the connection before trading.`});
      close();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return html`<form class="dialog-form" onSubmit=${save} autocomplete="off">
    <label class="field"><span class="field-label">Name</span>
      <input class="input" required maxlength="100" placeholder="For example, Trading" value=${draft.name} onInput=${e => set({name: e.currentTarget.value})} /></label>
    <fieldset class="field">
      <legend class="field-label">Kalshi environment</legend>
      ${editing ? html`<span>Demo or Live and the subaccount cannot change. Create a new connection for another one; history keeps its account.</span>` : null}
      <div class="choices">
        <label class="choice"><input type="radio" name="environment" disabled=${editing} checked=${!live} onChange=${() => set({connectionType: 'kalshi-demo', confirmLive: false})} />
          <span><strong>Kalshi Demo</strong><small>Virtual funds. The safe place to try your agents.</small></span></label>
        <label class="choice"><input type="radio" name="environment" disabled=${editing} checked=${live} onChange=${() => set({connectionType: 'kalshi-live'})} />
          <span><strong>Kalshi Live</strong><small>Real money. Approved trades are placed in your Kalshi account.</small></span></label>
      </div>
    </fieldset>
    <label class="field field-narrow"><span class="field-label">Subaccount</span>
      <input class="input" type="number" inputmode="numeric" min="0" max="63" step="1" disabled=${editing} value=${draft.subaccount} onInput=${e => set({subaccount: e.currentTarget.value})} />
      <span>0 is your primary account. Kalshi allows up to 63.</span></label>
    <div class="notice"><${Icon} name="info" /><span>Create an API key in your <a href=${KEY_PAGES[draft.connectionType]} target="_blank" rel="noopener noreferrer">Kalshi ${live ? 'Live' : 'Demo'} account</a>.
      Give it trading access only, with withdrawals off. GovernTrade stores it encrypted and never shows it again.</span></div>
    <label class="field"><span class="field-label">API key ID</span>
      <input class="input" spellcheck="false" placeholder=${account?.credentialMasks?.key_id ? `Saved: ${account.credentialMasks.key_id}` : ''} value=${draft.keyId} onInput=${e => set({keyId: e.currentTarget.value})} /></label>
    <label class="field"><span class="field-label">Private key</span>
      <textarea class="input mono-input" rows="5" spellcheck="false" placeholder="-----BEGIN RSA PRIVATE KEY-----" value=${draft.privateKey} onInput=${e => set({privateKey: e.currentTarget.value})}></textarea>
      <span>${account?.hasCredentials ? 'Leave both key fields empty to keep the saved keys. Enter both to rotate them.' : 'Paste the whole PEM file, including the BEGIN and END lines.'}</span></label>
    ${live ? html`<label class="confirm-row"><input type="checkbox" checked=${draft.confirmLive} onChange=${e => set({confirmLive: e.currentTarget.checked})} />
      <span>I understand this is real money. Trades that pass my rules, or that I approve, are placed in my Kalshi Live account.</span></label>` : null}
    <${ErrorNote} message=${error} />
    <div class="dialog-actions">
      <button type="button" class="btn btn-ghost" onClick=${close}>Cancel</button>
      <button class="btn btn-primary" disabled=${!valid || saving}>${saving ? html`<span class="spinner" aria-hidden="true"></span>Checking the keys` : editing ? 'Save' : 'Connect'}</button>
    </div>
  </form>`;
}

function Confirm({modal, close}) {
  const {busy} = useStore();
  const [error, setError] = useState('');
  const {type, account} = modal;
  const disconnect = type === 'disconnect';
  async function confirm() {
    setError('');
    try {
      await action(`account-${type}`, {id: account.id});
      setStore({toast: disconnect ? `${account.name} disconnected. Its agents are paused.` : `${account.name} removed.`});
      close();
    } catch (err) {
      setError(err.message);
    }
  }
  return html`<div class="dialog-form">
    <p>${disconnect
      ? `The saved keys are deleted and the agents on ${account.name} are paused. Open positions stay in your Kalshi account.`
      : `${account.name} leaves this workspace. Its orders and decisions stay in your activity history.`}</p>
    <${ErrorNote} message=${error} />
    <div class="dialog-actions">
      <button type="button" class="btn btn-ghost" onClick=${close}>Cancel</button>
      <button type="button" class="btn btn-danger" disabled=${busy} onClick=${confirm}>${disconnect ? 'Disconnect' : 'Remove'} ${account.name}</button>
    </div>
  </div>`;
}

export function Accounts({workspace}) {
  const {busy} = useStore();
  const [modal, setModal] = useState(null);
  const [error, setError] = useState('');
  const accounts = (workspace.accounts || []).filter(a => a.status !== 'removed');
  const close = () => setModal(null);

  async function refresh() {
    setError('');
    try {
      await action('refresh');
      setStore({toast: 'Balances refreshed.'});
    } catch (err) {
      setError(err.message);
    }
  }

  const title = modal?.type === 'edit' ? (modal.account ? `${modal.account.name} settings` : 'Connect Kalshi')
    : modal ? `${modal.type === 'disconnect' ? 'Disconnect' : 'Remove'} ${modal.account.name}?` : '';
  return html`<div class="section-stack">
    <div class="page-head">
      <p>Your money stays in your Kalshi accounts. GovernTrade places orders with trade-only keys.</p>
      <div class="page-actions">
        ${accounts.some(a => a.hasCredentials) ? html`<button class="btn" disabled=${busy} onClick=${refresh}>Refresh balances</button>` : null}
        <button class="btn btn-primary" onClick=${() => setModal({type: 'edit'})}>Connect Kalshi</button>
      </div>
    </div>
    <${ErrorNote} message=${error} />
    ${accounts.length ? accounts.map(a => html`<${AccountCard} key=${a.id} account=${a} open=${setModal} />`)
      : html`<div class="card"><div class="empty">
          <h3>No Kalshi account yet</h3>
          <p>Connect Kalshi Demo to try your agents with virtual funds, or Kalshi Live to trade real money.</p>
          <button class="btn btn-primary" onClick=${() => setModal({type: 'edit'})}>Connect Kalshi</button>
        </div></div>`}
    ${modal ? html`<${Dialog} title=${title} onClose=${close}>
      ${modal.type === 'edit' ? html`<${ConnectionForm} account=${modal.account} close=${close} />` : html`<${Confirm} modal=${modal} close=${close} />`}
    </${Dialog}>` : null}
  </div>`;
}
