// Integrity checks on the vendored force-graph module
// (scripts/frontend/force-graph.js). notes_graph.js imports it dynamically
// from the URL the graph partial puts on the canvas mount and constructs the
// default export.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const BUNDLE = path.join(
  REPO_ROOT, 'workspace', 'notes', 'ui', 'static', 'notes', 'ui', 'js', 'vendor', 'force-graph', 'force-graph.js'
);
const GRAPH_PARTIAL = path.join(
  REPO_ROOT, 'workspace', 'notes', 'ui', 'templates', 'notes', 'ui', 'partials', 'graph.html'
);
const GRAPH_SCRIPT = path.join(
  REPO_ROOT, 'workspace', 'notes', 'ui', 'static', 'notes', 'ui', 'js', 'notes_graph.js'
);

test('the bundle is an ES module with a default export', () => {
  assert.ok(fs.existsSync(BUNDLE), `missing artifact: ${BUNDLE}`);
  const src = fs.readFileSync(BUNDLE, 'utf8');
  assert.ok(fs.statSync(BUNDLE).size > 100_000, 'artifact suspiciously small');
  assert.match(src, /export\{\w+ as default\}/, 'no default export: notes_graph.js reads mod.default');
  assert.doesNotMatch(src, /^\/\/# sourceMappingURL=/m, 'source map reference left in');
  assert.doesNotMatch(src, /esm\.sh|cdn\.jsdelivr\.net|unpkg\.com/, 'CDN reference');
});

test('the graph partial hands the module URL to the script', () => {
  // The script is a static file and cannot resolve {% static %} itself; the
  // mount element carries the URL, and open() reads it back off the dataset.
  const partial = fs.readFileSync(GRAPH_PARTIAL, 'utf8');
  assert.match(
    partial,
    /x-ref="graphCanvas"[^>]*data-force-graph-url="\{% static 'notes\/ui\/js\/vendor\/force-graph\/force-graph\.js' %\}"/s
  );
  assert.match(fs.readFileSync(GRAPH_SCRIPT, 'utf8'), /import\(container\.dataset\.forceGraphUrl\)/);
});
