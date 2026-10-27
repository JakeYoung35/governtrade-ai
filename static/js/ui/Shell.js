// The signed-in frame: sidebar on desktop; top bar; bottom tab bar and a More sheet on mobile.
import {useEffect, useRef, useState} from 'preact/hooks';
import {html} from '../html.js';
import {Icon, BrandMark} from './icons.js';
import {signOut} from '../api.js';
import {setStore} from '../store.js';
import {navigate} from '../router.js';

const NAV = [
  {name: 'home', href: '/', label: 'Home', icon: 'home'},
  {name: 'approvals', href: '/approvals', label: 'Approvals', icon: 'approvals'},
  {name: 'agents', href: '/agents', label: 'Agents', icon: 'agents'},
  {name: 'activity', href: '/activity', label: 'Activity', icon: 'activity'},
  {name: 'rules', href: '/rules', label: 'Rules', icon: 'rules'},
  {name: 'accounts', href: '/accounts', label: 'Accounts', icon: 'accounts'},
];
const TABS = ['home', 'approvals', 'agents', 'activity'];
const SECTION = {'new-agent': 'agents', agent: 'agents'};

const awaiting = workspace => workspace.orderStats?.awaiting || 0;

function Count({n}) {
  return n ? html`<span class="nav-count num" aria-label=${`${n} waiting`}>${n}</span>` : null;
}

function MoneyPill({workspace}) {
  const accounts = (workspace.accounts || []).filter(a => a.status !== 'disconnected');
  if (accounts.some(a => a.environment === 'live')) return html`<span class="pill pill-ink">LIVE MONEY</span>`;
  if (accounts.length) return html`<span class="pill pill-outline">Demo money</span>`;
  return null;
}

function GovernorStatus({workspace}) {
  const running = workspace.permissions?.automation !== false;
  const version = workspace.rules?.version;
  return html`<span class=${'gov-status' + (running ? '' : ' paused')} title=${running ? 'Governor running' : 'All trading paused'}>
    <i aria-hidden="true"></i><span class="gov-text">${running ? 'Governor running' : 'All trading paused'}${version ? html`<span class="hide-mobile"> · rules v${version}</span>` : null}</span>
  </span>`;
}

async function doSignOut() {
  try {
    await signOut();
    navigate('/', {replace: true});
  } catch (e) {
    setStore({error: e.message, errorSource: 'auth'});
  }
}

function Who({auth}) {
  const name = auth.user?.name || auth.user?.email || 'Local workspace';
  const detail = auth.user ? auth.user.email : 'On this computer';
  return html`<div class="who"><strong>${name}</strong><small>${detail}</small></div>`;
}

function MoreSheet({current, onClose, auth}) {
  useEffect(() => {
    const onKey = e => e.key === 'Escape' && onClose();
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  }, []);
  const extra = NAV.filter(n => !TABS.includes(n.name));
  return html`<div class="sheet-layer">
    <div class="sheet-backdrop" onClick=${onClose}></div>
    <div class="sheet" role="dialog" aria-modal="true" aria-label="More">
      <div class="sheet-grip" aria-hidden="true"></div>
      ${extra.map(n => html`<a class="nav-link" href=${n.href} aria-current=${current === n.name ? 'page' : null} onClick=${onClose}><${Icon} name=${n.icon} />${n.label}</a>`)}
      <hr />
      <div class="sheet-who"><${Who} auth=${auth} /></div>
      <button class="nav-link" onClick=${() => { onClose(); doSignOut(); }}><${Icon} name="sign-out" />Sign out</button>
    </div>
  </div>`;
}

export function Shell({route, title, workspace, auth, children}) {
  const [more, setMore] = useState(false);
  const page = useRef(null);
  const current = SECTION[route.name] || route.name;
  const count = awaiting(workspace);
  const inMore = !TABS.includes(current) && NAV.some(n => n.name === current);
  // After moving to another page, focus its content so keyboard and screen reader users start there (opening the
  // order drawer changes only the query, so it does not count). The first page keeps the browser's own focus.
  // Entering the app (from the landing page, say) swaps in a shorter page; without this a phone keeps the old offset.
  useEffect(() => { window.scrollTo(0, 0); }, []);
  const where = `${route.name}:${JSON.stringify(route.params)}`;
  const shown = useRef(where);
  useEffect(() => {
    if (shown.current === where) return;
    shown.current = where;
    page.current?.focus({preventScroll: true});
  }, [where]);
  const skip = e => { e.preventDefault(); page.current?.focus(); };
  return html`<div class="app">
    <a class="skip-link" href="#content" onClick=${skip}>Skip to content</a>
    <nav class="sidenav" aria-label="Main">
      <a class="brand" href="/"><${BrandMark} />GovernTrade</a>
      ${NAV.map(n => html`<a class="nav-link" href=${n.href} aria-current=${current === n.name ? 'page' : null}>
        <${Icon} name=${n.icon} />${n.label}${n.name === 'approvals' ? html`<${Count} n=${count} />` : null}
      </a>`)}
      <div class="sidenav-foot">
        <${Who} auth=${auth} />
        <button class="icon-btn" title="Sign out" aria-label="Sign out" onClick=${doSignOut}><${Icon} name="sign-out" /></button>
      </div>
    </nav>
    <div class="main">
      <header class="topbar">
        <${BrandMark} />
        <h1>${title}</h1>
        <${MoneyPill} workspace=${workspace} />
        <span class="spacer"></span>
        <${GovernorStatus} workspace=${workspace} />
      </header>
      <main class="page" id="content" tabindex="-1" ref=${page}>${children}</main>
    </div>
    <nav class="tabbar" aria-label="Main">
      ${NAV.filter(n => TABS.includes(n.name)).map(n => html`<a class="tab" href=${n.href} aria-current=${current === n.name ? 'page' : null}>
        <${Icon} name=${n.icon} />${n.label}${n.name === 'approvals' ? html`<${Count} n=${count} />` : null}
      </a>`)}
      <button class="tab" aria-current=${inMore ? 'page' : null} aria-expanded=${more} onClick=${() => setMore(true)}><${Icon} name="more" />More</button>
    </nav>
    ${more ? html`<${MoreSheet} current=${current} auth=${auth} onClose=${() => setMore(false)} />` : null}
  </div>`;
}
