import {html} from '../html.js';
const VERDICTS = {ALLOW: ['Allowed', 'allow'], REQUIRE_APPROVAL: ['Needs approval', 'review'], BLOCK: ['Blocked', 'block']};
export const verdictLabel = value => (VERDICTS[value] || [value || 'Not evaluated'])[0];
export function Verdict({value}) {
  const [label, style] = VERDICTS[value] || [verdictLabel(value), 'neutral'];
  return html`<span class=${`pill pill-${style}`}>${label}</span>`;
}
