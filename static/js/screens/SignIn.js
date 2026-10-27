// Full-page states before the workspace: loading, unavailable, and the three ways in (local, Google, Auth0).
import {useEffect, useRef, useState} from 'preact/hooks';
import {html} from '../html.js';
import {Icon, BrandMark} from '../ui/icons.js';
import {enterLocalWorkspace, googleCredential, initializeAuth, signinNotice} from '../api.js';
import {setStore} from '../store.js';

const Brand = () => html`<span class="brand"><${BrandMark} />GovernTrade<small>AI</small></span>`;

function ErrorNote({message}) {
  return message ? html`<div class="notice notice-error" role="alert"><${Icon} name="alert" />${message}</div>` : null;
}

function Intro({lead}) {
  return html`<${Brand} />
    <h1>Your markets.<br />Your agents. Your rules.</h1>
    <p class="lead">${lead}</p>`;
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

export function LocalEntry() {
  const [entering, setEntering] = useState(false);
  return html`<div class="solo"><div class="signin">
    <${Intro} lead="Connect your Kalshi account, create agents and set the boundaries for every trade." />
    <div class="actions">
      <button class="btn btn-primary btn-lg btn-block" disabled=${entering} onClick=${() => { setEntering(true); enterLocalWorkspace(); }}>
        ${entering ? html`<span class="spinner" aria-hidden="true"></span>Opening` : html`Enter local workspace<${Icon} name="arrow" />`}
      </button>
    </div>
    <p class="fine">This app runs on this computer. It is not a hosted multi-user sign-in.</p>
  </div></div>`;
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
  const lead = 'Sign in to your personal trading workspace. Connect Kalshi, create agents and set the boundaries for every trade.';
  return html`<div class="solo"><div class="signin">
    <${Intro} lead=${lead} />
    <div class="actions">
      ${auth.mode === 'auth0'
        ? html`<a class="btn btn-primary btn-lg btn-block" href=${auth.config.loginUrl} target="_self">Sign in<${Icon} name="arrow" /></a>`
        : html`<${GoogleButton} config=${auth.config} />`}
    </div>
    <${ErrorNote} message=${message} />
    <p class="fine">Your accounts, API keys, agents and trade history belong to your workspace. There is no shared website password.</p>
  </div></div>`;
}
