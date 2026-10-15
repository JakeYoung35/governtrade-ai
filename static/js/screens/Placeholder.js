// Screens not rebuilt yet on this branch, and unknown paths.
import {html} from '../html.js';

export function Placeholder({title}) {
  return html`<div class="card"><div class="empty">
    <h3>${title} is being rebuilt</h3>
    <p>This screen arrives in a later phase of the redesign.</p>
    <a class="btn" href="/">Go to Home</a>
  </div></div>`;
}

export function NotFound() {
  return html`<div class="card"><div class="empty">
    <h3>Page not found</h3>
    <p>That address does not match any page in GovernTrade.</p>
    <a class="btn" href="/">Go to Home</a>
  </div></div>`;
}
