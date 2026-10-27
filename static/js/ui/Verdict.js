import {html} from '../html.js';
const VERDICTS = {ALLOW: ['Allowed', 'allow'], REQUIRE_APPROVAL: ['Needs approval', 'review'], BLOCK: ['Blocked', 'block']};
export const verdictLabel = value => (VERDICTS[value] || [value || 'Not evaluated'])[0];
// With the order's `status`, an approval that was denied or ran out reads in the past tense, so it does not sit beside
// "Cancelled" or "Denied" still asking for the user.
export function Verdict({value, status}) {
  let [label, style] = VERDICTS[value] || [verdictLabel(value), 'neutral'];
  if (value === 'REQUIRE_APPROVAL' && status && status !== 'awaiting approval') [label, style] = ['Needed approval', 'neutral'];
  return html`<span class=${`pill pill-${style}`}>${label}</span>`;
}
