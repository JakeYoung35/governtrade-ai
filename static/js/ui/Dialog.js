import {useEffect, useRef} from 'preact/hooks';
import {html} from '../html.js';
export function Dialog({title, onClose, children}) {
  const ref = useRef(null);
  useEffect(() => { const focus = document.activeElement; const overflow = document.body.style.overflow; ref.current.showModal(); document.body.style.overflow = 'hidden'; return () => {document.body.style.overflow = overflow; if (focus?.isConnected) focus.focus();}; }, []);
  return html`<dialog class="form-dialog" ref=${ref} aria-label=${title} onCancel=${e => {e.preventDefault(); onClose();}}><div class="card-head"><h2>${title}</h2><button class="btn btn-ghost" onClick=${onClose}>Close</button></div><div class="card-body section-stack">${children}</div></dialog>`;
}
