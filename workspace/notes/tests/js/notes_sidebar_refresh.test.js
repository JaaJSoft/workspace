'use strict';

// refreshSidebar() re-renders the sidebar (folder tree, tags, group folders)
// from the server after anything changes it: a rename, a folder creation, a
// tag edit, an SSE resync. The swap itself goes through alpine-ajax (which
// merges the response's #notes-sidebar and drops stale overlapping requests);
// what this file pins is the wiring around it: the request targets the
// sidebar element, the folder tree state is reloaded from the new embedded
// JSON after the swap, and a failed refresh leaves the current state alone.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function makeApp({ ajaxFails = false } = {}) {
  const calls = { ajax: [], folderLoads: 0, order: [] };

  const ctx = loadScript('workspace/notes/ui/static/notes/ui/js/notes.js', {
    fetch: () => Promise.resolve({ ok: false }),
    localStorage: { getItem: () => null, setItem: () => {} },
    addEventListener: () => {},
    document: { getElementById: () => null },
    tagsMixin: () => ({}),
    viewerPanelMixin: () => ({}),
  });

  const app = ctx.notesApp({});
  app._loadedChildren = { 'folder-1': true };
  app._loadFolderData = () => {
    calls.folderLoads++;
    calls.order.push('loadFolderData');
  };
  app._restoreExpandedFolders = async () => { calls.order.push('restoreExpanded'); };
  app.$ajax = async (url, options) => {
    calls.ajax.push({ url, options });
    if (ajaxFails) throw new Error('Target [#notes-sidebar] was not found in response');
  };
  return { app, calls };
}

test('refreshSidebar swaps the /notes partial into the sidebar element', async () => {
  const { app, calls } = makeApp();

  await app.refreshSidebar();

  assert.equal(calls.ajax.length, 1);
  assert.equal(calls.ajax[0].url, '/notes');
  assert.equal(calls.ajax[0].options.target, 'notes-sidebar');
  assert.equal(
    calls.ajax[0].options.focus,
    false,
    'a background refresh must not steal focus'
  );
});

test('refreshSidebar reloads the folder tree state after the swap', async () => {
  const { app, calls } = makeApp();

  await app.refreshSidebar();

  assert.deepStrictEqual(
    { ...app._loadedChildren },
    {},
    'lazily-fetched children must be re-fetched against the new tree'
  );
  assert.equal(calls.folderLoads, 1, 'folder data is re-read from the new embedded JSON');
});

test('refreshSidebar restores expanded folders against the new tree', async () => {
  // Regression: the fresh folder JSON only carries roots, so the children of
  // expanded folders must be re-fetched after the reload - without it their
  // rows vanish until the user collapses and expands them again.
  const { app, calls } = makeApp();

  await app.refreshSidebar();

  assert.deepStrictEqual(
    Array.from(calls.order),
    ['loadFolderData', 'restoreExpanded'],
    'expanded folders reload after (not before) the tree is replaced'
  );
});

test('a failed refresh leaves the folder state untouched', async () => {
  const { app, calls } = makeApp({ ajaxFails: true });

  await app.refreshSidebar();

  assert.deepStrictEqual({ ...app._loadedChildren }, { 'folder-1': true });
  assert.equal(calls.folderLoads, 0);
  assert.deepStrictEqual(Array.from(calls.order), []);
});
