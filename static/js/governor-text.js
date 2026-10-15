// Governor and order wording shared by every screen, so a reason code or a status reads the same everywhere.

// Why the governor resized or rejected a proposal (governor/rules.py reason codes).
const REASONS = {
  noNetEdge: 'No edge after fees',
  insufficientLiquidity: 'Not enough liquidity',
  priceAboveCeiling: 'Price above the limit',
  positionAlreadyOpen: 'Position already open',
  marketExposureCap: 'Market exposure cap',
  agentExposureCap: 'Agent exposure cap',
  eventGroupCap: 'Event exposure cap',
  totalExposureCap: 'Total exposure cap',
  cashLimit: 'Not enough cash',
  zeroSize: 'Stake rounds to zero',
  conflict: 'Conflicts with another proposal',
  redundantSignal: 'Duplicates another proposal',
  paused: 'Trading paused',
  lossBreakerLatched: 'Loss breaker is on',
  staleValuation: 'Balance out of date',
  noEquity: 'No account equity',
  marketNotInSnapshot: 'Market data unavailable',
  marketClosed: 'Market closed',
  marketNotTradable: 'Market not tradable',
  proposalExpired: 'Proposal expired',
  staleQuote: 'Price data too old',
};

// An unknown code still reads as words: "someNewCode" becomes "Some new code".
export function reasonText(code) {
  if (REASONS[code]) return REASONS[code];
  const words = String(code).replace(/([a-z])([A-Z])/g, '$1 $2').replaceAll('_', ' ').toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// [label, pill tone] for an order status, or a chat proposal status before an order exists.
const STATUSES = {
  queued: ['With the governor', 'review'],
  approved: ['Placing order', 'review'],
  rejected: ['Rejected', 'block'],
  executionFailed: ['Order failed', 'block'],
  'awaiting approval': ['Needs approval', 'review'],
  resting: ['Resting', 'neutral'],
  submitted: ['Submitted', 'neutral'],
  open: ['Open', 'neutral'],
  'partially filled': ['Partly filled', 'neutral'],
  filled: ['Filled', 'allow'],
  blocked: ['Blocked', 'block'],
  denied: ['Denied', 'block'],
  expired: ['Expired', 'neutral'],
  cancelled: ['Cancelled', 'neutral'],
  canceled: ['Cancelled', 'neutral'],
  'submission unknown': ['Unconfirmed', 'review'],
  unknown: ['Unconfirmed', 'review'],
  failed: ['Failed', 'block'],
};

export function statusOf(status) {
  const [label, tone] = STATUSES[status] || [status ? status.charAt(0).toUpperCase() + status.slice(1) : 'Unknown', 'neutral'];
  return {label, tone};
}
