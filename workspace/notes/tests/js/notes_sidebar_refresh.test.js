'use strict';

// refreshSidebar() re-renders the sidebar (folder tree, tags, group folders)
// from the server after anything changes it: a rename, a folder creation, a
// tag edit, an SSE resync. Beyond the swap itself, it must reload the folder
// tree state from the embedded JSON that ships inside the partial - and a
// failed refresh must leave the current sidebar and folder state alone.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function makeApp({ ok = true, html = '<ul></ul>' } = {}) {
  const calls = { requests: [], folderLoads: 0 };
  const container = {
    textContent: 'stale sidebar',
    appended: null,
    appendChild(node) { this.appended = node; },
  };

  const ctx = loadScript('workspace/notes/ui/static/notes/ui/js/notes.js', {
    fetch: (url, options) => {
      if (url !== '/notes') return Promise.resolve({ ok: false });
      calls.requests.push({ url, options });
      return Promise.resolve({ ok, text: async () => html });
    },
    localStorage: { getItem: () => null, setItem: () => {} },
    addEventListener: () => {},
    document: {
      getElementById: (id) => (id === 'notes-sidebar' ? container : null),
      createElement: () => {
        const tmpl = { innerHTML: '' };
        Object.defineProperty(tmpl, 'content', {
          get() { return { html: tmpl.innerHTML }; },
        });
        return tmpl;
      },
    },
    tagsMixin: () => ({}),
  });

  const app = ctx.notesApp({});
  app._loadedChildren = { 'folder-1': true };
  app._loadFolderData = () => { calls.folderLoads++; };
  return { app, calls, container };
}

test('refreshSidebar swaps the /notes partial into the sidebar container', async () => {
  const { app, calls, container } = makeApp({ html: '<ul>fresh</ul>' });

  await app.refreshSidebar();

  assert.equal(calls.requests.length, 1);
  assert.equal(calls.requests[0].url, '/notes');
  assert.ok(
    calls.requests[0].options.headers['X-Alpine-Request'],
    'must request the sidebar partial, not the full notes page'
  );
  assert.equal(container.textContent, '', 'the stale content is cleared');
  assert.equal(container.appended.html, '<ul>fresh</ul>');
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

test('a failed refresh leaves the sidebar and folder state untouched', async () => {
  const { app, calls, container } = makeApp({ ok: false });

  await app.refreshSidebar();

  assert.equal(container.textContent, 'stale sidebar');
  assert.deepStrictEqual({ ...app._loadedChildren }, { 'folder-1': true });
  assert.equal(calls.folderLoads, 0);
});
