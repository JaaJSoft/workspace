'use strict';

// The sidebar folder tree renders as a single flat x-for over
// visibleFolderRows(): the preorder walk of the lazy-loaded tree, pruned
// at collapsed folders and (unless showHidden) at hidden folders. Depth
// is unbounded - these tests pin the row computation that replaced the
// hardcoded 4-level template (issue #271).

const assert = require('node:assert');
const { test } = require('node:test');

const { loadScript } = require('../../../common/tests/js/loader');

function makeApp() {
  const ctx = loadScript('workspace/notes/ui/static/notes/ui/js/notes.js', {
    fetch: () => Promise.resolve({ ok: false }),
    localStorage: { getItem: () => null, setItem: () => {} },
    addEventListener: () => {},
    document: { getElementById: () => null },
    // Defined by files/ui/js/tags.js in the browser; irrelevant here.
    tagsMixin: () => ({}),
    viewerPanelMixin: () => ({}),
  });
  return ctx.notesApp({});
}

function folder(uuid, depth, children) {
  const f = { uuid, name: uuid, depth };
  if (children) {
    f.children = children;
    f.has_children = true;
  }
  return f;
}

// Normalize to a test-realm array of uuids (vm arrays fail the
// deepStrictEqual prototype check against test-side literals).
function rowUuids(app, list) {
  return Array.from(app.visibleFolderRows(list), (r) => r.uuid);
}

test('returns only roots when nothing is expanded', () => {
  const app = makeApp();
  const list = [folder('a', 0, [folder('a1', 1)]), folder('b', 0)];

  assert.deepStrictEqual(rowUuids(app, list), ['a', 'b']);
});

test('flattens expanded folders in preorder', () => {
  const app = makeApp();
  const list = [
    folder('a', 0, [folder('a1', 1), folder('a2', 1)]),
    folder('b', 0, [folder('b1', 1)]),
  ];
  app.expandedFolders = ['a'];

  assert.deepStrictEqual(rowUuids(app, list), ['a', 'a1', 'a2', 'b']);
});

test('keeps loaded children of a collapsed folder out of the rows', () => {
  const app = makeApp();
  const list = [folder('a', 0, [folder('a1', 1, [folder('a1x', 2)])])];
  // a1's children are loaded, but only a is expanded.
  app.expandedFolders = ['a'];

  assert.deepStrictEqual(rowUuids(app, list), ['a', 'a1']);
});

test('renders arbitrarily deep chains (no 4-level cap)', () => {
  const app = makeApp();
  const chain = folder('l0', 0, [
    folder('l1', 1, [
      folder('l2', 2, [
        folder('l3', 3, [folder('l4', 4, [folder('l5', 5)])]),
      ]),
    ]),
  ]);
  app.expandedFolders = ['l0', 'l1', 'l2', 'l3', 'l4'];

  assert.deepStrictEqual(rowUuids(app, [chain]), [
    'l0', 'l1', 'l2', 'l3', 'l4', 'l5',
  ]);
});

test('prunes hidden subtrees unless showHidden is on', () => {
  const app = makeApp();
  const list = [
    folder('a', 0, [folder('a1', 1)]),
    folder('h', 0, [folder('h1', 1)]),
  ];
  app.expandedFolders = ['a', 'h'];
  app.notePrefs.hiddenItems = ['h'];

  assert.deepStrictEqual(rowUuids(app, list), ['a', 'a1']);

  app.notePrefs.showHidden = true;
  assert.deepStrictEqual(rowUuids(app, list), ['a', 'a1', 'h', 'h1']);
});
