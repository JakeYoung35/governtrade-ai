// The toast: rises in from the bottom and drops away faster when it goes (.toast in components.css). It keeps the last
// message on screen through its exit, and the live region stays mounted so a screen reader announces each message.
import {useEffect, useState} from 'preact/hooks';
import {html} from '../html.js';

const EXIT_MS = 160;  // matches .toast.leaving

export function Toast({message, onDismiss}) {
  const [shown, setShown] = useState(message);
  const [leaving, setLeaving] = useState(false);
  useEffect(() => {
    if (message) { setShown(message); setLeaving(false); return undefined; }
    if (!shown) return undefined;
    setLeaving(true);
    const timer = setTimeout(() => { setShown(''); setLeaving(false); }, EXIT_MS);
    return () => clearTimeout(timer);
  }, [message]);
  return html`<div class="toast-region" role="status">
    ${shown ? html`<div key=${shown} class=${'toast' + (leaving ? ' leaving' : '')}>${shown}
      <button class="btn btn-ghost btn-sm" disabled=${leaving} onClick=${onDismiss}>Dismiss</button></div>` : null}
  </div>`;
}
