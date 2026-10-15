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

// Cash + reserved + marked positions of one account balance, or null when the balance is unknown.
export const accountValue = b => b ? Number(b.cash || 0) + Number(b.reserved || 0) + Number(b.positionValue || 0) : null;

// The combined value of every connected account with a known balance, or null when none is known.
export function workspaceValue(workspace) {
  const known = (workspace?.accounts || [])
    .filter(a => !['removed', 'disconnected'].includes(a.status) && a.balance)
    .map(a => accountValue(a.balance));
  return known.length ? known.reduce((n, v) => n + v, 0) : null;
}
