// Formatting shared by every screen. Numbers stay plain: no rounding that could misstate money.

export const money = n => n == null || n === '' || !Number.isFinite(Number(n)) ? '–' : Number(n).toLocaleString('en-US', {style: 'currency', currency: 'USD'});

export function when(t) {
  if (t == null || t === '') return '';
  const d = new Date(t);
  return isNaN(d) ? String(t) : d.toLocaleString('en-US', {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'});
}

export function duration(seconds) {
  const v = Number(seconds);
  const one = (n, unit) => `${+n.toFixed(1)} ${unit}${+n.toFixed(1) === 1 ? '' : 's'}`;
  return v < 60 ? one(v, 'second') : v < 3600 ? one(v / 60, 'minute') : one(v / 3600, 'hour');
}

const finite = v => v !== null && v !== undefined && v !== '' && typeof v !== 'boolean' && Number.isFinite(Number(v));

// Cash + reserved + marked positions of one account balance, as the governor values it (governor_service._equity):
// cash and positions are required, a missing reserved amount counts as 0. Null when the balance is missing or any
// figure is unusable, never a made-up $0.
export function accountValue(b) {
  if (!b || !finite(b.cash) || !finite(b.positionValue) || (b.reserved != null && !finite(b.reserved))) return null;
  return Number(b.cash) + Number(b.reserved ?? 0) + Number(b.positionValue);
}

// The combined value of the connected accounts, or null when any of them has no usable balance (a partial total would
// understate the workspace, so it is never shown), as the server's summary does.
export function workspaceValue(workspace) {
  const values = (workspace?.accounts || [])
    .filter(a => !['removed', 'disconnected'].includes(a.status))
    .map(a => accountValue(a.balance));
  return values.length && values.every(v => v != null) ? values.reduce((n, v) => n + v, 0) : null;
}
