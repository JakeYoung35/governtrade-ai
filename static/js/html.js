// JSX-like templates without a build step: html`<div class=${x}>...</div>` creates Preact elements.
import {h} from 'preact';
import htm from 'htm';

export const html = htm.bind(h);
