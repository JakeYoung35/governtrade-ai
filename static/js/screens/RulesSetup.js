// First login (or rules converted from the old dollar limits): set the trading rules before anything trades.
import {html} from '../html.js';
import {Icon, BrandMark} from '../ui/icons.js';
import {RulesForm} from '../ui/RulesForm.js';
import {signOut} from '../api.js';
import {setStore} from '../store.js';
import {workspaceValue} from '../format.js';
import {navigate} from '../router.js';

function BalanceNote({workspace}) {
  const value = workspaceValue(workspace);
  if (value == null) return html`<div class="notice"><${Icon} name="info" />No account balance yet, so the dollar examples use a $1,000 account.</div>`;
  if (value === 0) return html`<div class="notice"><${Icon} name="info" />Your account is empty, so every limit is $0 until you add funds. The percentages still apply as your balance grows.</div>`;
  return null;
}

export function RulesSetup({workspace}) {
  const converted = workspace.rules?.converted;
  const out = () => signOut().catch(e => setStore({error: e.message}));
  return html`<div>
    <header class="setup-top">
      <span class="brand"><${BrandMark} />GovernTrade</span>
      <button class="btn btn-ghost" onClick=${out}><${Icon} name="sign-out" />Sign out</button>
    </header>
    <main class="setup">
      <span class="eyebrow">Before any trading</span>
      <h1>${converted ? 'Review your trading rules' : 'Set your trading rules'}</h1>
      <p class="lead">These rules decide how big each trade is, how much of your account can be at risk, and when trading stops. Each starts at a suggested position; slide to adjust and see what it means for your account. Nothing trades until you save.</p>
      ${converted ? html`<div class="notice notice-warn"><${Icon} name="info" />Your rules were in dollars before. They are now shares of your account, converted at its current value; where a dollar limit and a percentage cap became one rule, the stricter one was kept. Check each one, then save.</div>` : null}
      <${BalanceNote} workspace=${workspace} />
      <${RulesForm} workspace=${workspace} setup onSaved=${() => navigate('/', {replace: true})} />
    </main>
  </div>`;
}
