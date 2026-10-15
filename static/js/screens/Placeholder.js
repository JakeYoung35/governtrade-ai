// The guided demo (deferred: it is not in the navigation) and unknown paths.
import {html} from '../html.js';

export function Placeholder() {
  return html`<div class="card"><div class="empty">
    <h3>The guided demo is not available yet</h3>
    <p>To try your agents without real money, connect Kalshi Demo. It uses virtual funds.</p>
    <a class="btn btn-primary" href="/accounts">Connect Kalshi Demo</a>
  </div></div>`;
}

export function NotFound() {
  return html`<div class="card"><div class="empty">
    <h3>Page not found</h3>
    <p>That address does not match any page in GovernTrade.</p>
    <a class="btn" href="/">Go to Home</a>
  </div></div>`;
}
