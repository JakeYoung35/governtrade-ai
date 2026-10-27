// Line icons on a 24px grid, 1.75 stroke, drawn to match each other. Usage: html`<${Icon} name="home" />`.
import {html} from '../html.js';

const PATHS = {
  home: 'M4 10.5 12 4l8 6.5V20H4z M9.5 20v-5.5h5V20',
  approvals: 'M9 12.5l2 2 4-4.5 M12 3.5l7 2.8v5.2c0 4.4-2.9 7.9-7 9-4.1-1.1-7-4.6-7-9V6.3z',
  agents: 'M7 8h10a3 3 0 0 1 3 3v5a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3v-5a3 3 0 0 1 3-3z M12 8V5 M12 4.5h.01 M9.5 13.5h.01 M14.5 13.5h.01',
  activity: 'M4 6h16 M4 12h16 M4 18h10',
  rules: 'M5 7h8 M17 7h2 M5 17h2 M11 17h8 M15 5v4 M9 15v4',
  accounts: 'M4 7.5A1.5 1.5 0 0 1 5.5 6h13A1.5 1.5 0 0 1 20 7.5v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 16.5z M4 10h16 M7.5 14.5h3',
  more: 'M5 12h.01 M12 12h.01 M19 12h.01',
  'sign-out': 'M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4 M10 16l-4-4 4-4 M6 12h10',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 11v5 M12 7.5h.01',
  alert: 'M12 4 2.8 19.5h18.4z M12 10v4 M12 16.8h.01',
  arrow: 'M5 12h14 M13 6l6 6-6 6',
  mark: 'M12 3 4 7v5c0 4.6 3.4 8.3 8 9 4.6-.7 8-4.4 8-9V7z',
};

export function Icon({name, title}) {
  const label = title ? {role: 'img', 'aria-label': title} : {'aria-hidden': 'true'};
  return html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" focusable="false" ...${label}>
    ${PATHS[name].split(' M').map((d, i) => html`<path d=${i ? 'M' + d : d} />`)}
  </svg>`;
}

// The brand mark: a filled shield on ink.
export function BrandMark() {
  return html`<span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 3 4 7v5c0 4.6 3.4 8.3 8 9 4.6-.7 8-4.4 8-9V7z" fill="currentColor" /></svg></span>`;
}
