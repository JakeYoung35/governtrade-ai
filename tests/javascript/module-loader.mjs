// Resolve the browser import map for Node tests without installing another Preact copy.
export async function resolve(specifier, context, nextResolve) {
  const files = {'preact': 'preact.module.js', 'preact/hooks': 'preact-hooks.module.js'};
  if (files[specifier]) return {url: new URL(`../../static/vendor/${files[specifier]}`, import.meta.url).href, shortCircuit: true};
  return nextResolve(specifier, context);
}
