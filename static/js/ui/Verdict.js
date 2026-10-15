import {html} from '../html.js';
const VERDICTS = {ALLOW: ['Allowed', 'allow'], REQUIRE_APPROVAL: ['Needs approval', 'review'], BLOCK: ['Blocked', 'block']};
export function Verdict({value}) {
  const [label, style] = VERDICTS[value] || [value || 'Not evaluated', 'neutral'];
  return html`<span class=${`pill pill-${style}`}>${label}</span>`;
}
