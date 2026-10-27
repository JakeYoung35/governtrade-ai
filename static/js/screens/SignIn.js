// Full-page states before the workspace: loading, unavailable, and the landing page with the three ways in (local, Google, Auth0).
import {useEffect, useRef, useState} from 'preact/hooks';
import {html} from '../html.js';
import {Icon, BrandMark} from '../ui/icons.js';
import {enterLocalWorkspace, googleCredential, initializeAuth, signinNotice} from '../api.js';
import {setStore} from '../store.js';
import {Landing} from './Landing.js';

const Brand = () => html`<span class="brand"><${BrandMark} />GovernTrade<small>AI</small></span>`;

function ErrorNote({message}) {
  return message ? html`<div class="notice notice-error" role="alert"><${Icon} name="alert" />${message}</div>` : null;
}

export function Loading() {
  return html`<div class="solo"><div class="loading" role="status"><span class="spinner" aria-hidden="true"></span>Loading your workspace</div></div>`;
}

export function Unavailable({error}) {
  const [trying, setTrying] = useState(false);
  async function retry() {
    setTrying(true);
    setStore({error: '', errorSource: ''});
    await initializeAuth();
    setTrying(false);
  }
  return html`<div class="solo"><div class="signin">
    <${Brand} />
    <h1>Workspace unavailable</h1>
    <p class="lead">${error || 'GovernTrade could not load your workspace.'}</p>
    <div class="actions"><button class="btn btn-primary btn-lg btn-block" onClick=${retry} disabled=${trying}>
      ${trying ? html`<span class="spinner" aria-hidden="true"></span>Trying again` : 'Try again'}
    </button></div>
  </div></div>`;
}

function LocalAction({size}) {
  const [entering, setEntering] = useState(false);
  return html`<button class=${'btn btn-primary btn-' + size} disabled=${entering} onClick=${() => { setEntering(true); enterLocalWorkspace(); }}>
    ${entering ? html`<span class="spinner" aria-hidden="true"></span>Opening` : html`Enter local workspace<${Icon} name="arrow" />`}
  </button>`;
}

export function LocalEntry() {
  return html`<${Landing} action=${size => html`<${LocalAction} size=${size} />`} repeatable
    lead="Hire a crew of AI agents to trade prediction markets. They pitch trades; the governor sizes every one and holds it to the limits you set."
    fine="This app runs on this computer. It is not a hosted multi-user sign-in." />`;
}

function GoogleButton({config}) {
  const slot = useRef(null);
  const [status, setStatus] = useState('Loading Google sign-in');
  useEffect(() => {
    let live = true;
    const mount = () => {
      if (!live || !slot.current) return;
      google.accounts.id.initialize({client_id: config.clientId, nonce: config.nonce, auto_select: false, callback: googleCredential});
      google.accounts.id.renderButton(slot.current, {theme: 'outline', size: 'large', text: 'continue_with', shape: 'rectangular', width: 320});
      setStatus('');
    };
    if (window.google?.accounts?.id) mount();
    else {
      document.getElementById('google-identity-library')?.remove();
      const script = Object.assign(document.createElement('script'), {id: 'google-identity-library', src: 'https://accounts.google.com/gsi/client', async: true});
      script.onload = mount;
      script.onerror = () => live && setStatus('Google sign-in could not load. Check your connection and reload.');
      document.head.appendChild(script);
    }
    return () => { live = false; };
  }, [config.nonce]);
  return html`<div class="google-slot" ref=${slot}></div>${status ? html`<p class="signin-status" role="status">${status}</p>` : null}`;
}

export function SignedOut({auth, error}) {
  const message = error || signinNotice();
  // Auth0 is a plain link and can appear more than once; Google's button mounts into a single slot.
  const action = auth.mode === 'auth0'
    ? size => html`<a class=${'btn btn-primary btn-' + size} href=${auth.config.loginUrl} target="_self">Sign in<${Icon} name="arrow" /></a>`
    : () => html`<${GoogleButton} config=${auth.config} />`;
  return html`<${Landing} action=${action} repeatable=${auth.mode === 'auth0'}
    lead="Hire a crew of AI agents to trade prediction markets. Connect Kalshi, and the governor sizes every trade they pitch and holds it to the limits you set."
    notice=${html`<${ErrorNote} message=${message} />`}
    fine="Your accounts, API keys, agents and trade history belong to your workspace. There is no shared website password." />`;
}
