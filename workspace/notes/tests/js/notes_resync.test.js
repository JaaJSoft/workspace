'use strict';

// resync() is what notes runs when the SSE stream comes back up - a resumed
// tab, or a bfcache restore after a mobile back gesture. It refreshes both
// the sidebar (folder tree, tag counts) and the note list for the current
// view, so the page stops showing what it held before the freeze.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function makeApp(view) {
  const ctx = loadScript('workspace/notes/ui/static/notes/ui/js/notes.js', {
    fetch: () => Promise.resolve({ ok: false }),
    localStorage: { getItem: () => null, setItem: () => {} },
    addEventListener: () => {},
    document: { getElementById: () => null },
    tagsMixin: () => ({}),
    viewerPanelMixin: () => ({}),
  });

  const calls = { sidebar: 0, urls: [] };
  const app = ctx.notesApp({});
  app.activeView = view;
  app.refreshSidebar = () => { calls.sidebar++; };
  app.loadNotes = (url) => { calls.urls.push(url); };
  app._buildNotesUrl = () => `/api/v1/files?view=${view}`;
  return { app, calls };
}

test('resync refreshes the sidebar and reloads the current view', () => {
  const { app, calls } = makeApp('favorites');

  app.resync();

  assert.equal(calls.sidebar, 1);
  assert.deepStrictEqual(Array.from(calls.urls), ['/api/v1/files?view=favorites']);
});

test('resync reloads through the URL builder so filters survive', () => {
  const { app, calls } = makeApp('folder');
  app._buildNotesUrl = () => '/api/v1/files?parent=abc&search=draft';

  app.resync();

  assert.deepStrictEqual(
    Array.from(calls.urls),
    ['/api/v1/files?parent=abc&search=draft'],
    'the active filters must not be dropped on reconnect'
  );
});

test('resync leaves the graph view alone', () => {
  const { app, calls } = makeApp('graph');

  app.resync();

  assert.equal(calls.sidebar, 1, 'the sidebar still refreshes');
  assert.deepStrictEqual(
    Array.from(calls.urls),
    [],
    'the graph owns its own data and is not driven by loadNotes()'
  );
});
