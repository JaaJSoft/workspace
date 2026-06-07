'use strict';

// Regression test: applying a saved defaultView preference in notesApp.init()
// must not throw. initialView was declared `const` in the factory scope but
// reassigned inside init() (the prefs fetch resolves after the component is
// created, so init() re-resolves the view). Assigning to a const throws
// TypeError, aborting init() halfway: no event listeners, no initial notes
// load, for every user whose saved default view is not 'all'.

const assert = require('node:assert');
const { test } = require('node:test');

const { loadScript } = require('../../../common/tests/js/loader');

function loadNotes() {
  return loadScript('workspace/notes/ui/static/notes/ui/js/notes.js', {
    fetch: () => Promise.resolve({ ok: false }),
    localStorage: { getItem: () => null, setItem: () => {} },
    addEventListener: () => {},
    document: { getElementById: () => null },
    // Defined by files/ui/js/tags.js in the browser; its contents are
    // irrelevant to view resolution.
    tagsMixin: () => ({}),
  });
}

function stubBrowserBits(app) {
  // Neutralize the parts of init() that hit the DOM or the network;
  // the test only cares about the view resolution logic.
  app._loadFolderData = () => {};
  app.loadTags = async () => {};
  app._restoreExpandedFolders = async () => {};
  app.setView = async () => {};
  app.openJournal = async () => {};
  app.refreshSidebar = () => {};
  app.$nextTick = () => {};
}

test('init applies a saved defaultView pref when the URL has no view', async () => {
  const ctx = loadNotes();

  // Mirror the real race: the component is created while the prefs cache
  // still holds the defaults (the eager fetch has not resolved yet)...
  const app = ctx.notesApp({});
  stubBrowserBits(app);
  assert.equal(app.activeView, 'all');

  // ...then the prefs fetch lands with a non-default view before init().
  ctx._notesPrefsCache.defaultView = 'favorites';

  await app.init();

  assert.equal(app.activeView, 'favorites');
  assert.equal(app.viewTitle, 'Favorites');
});

test('init keeps the URL view over the saved pref', async () => {
  const ctx = loadNotes();

  const app = ctx.notesApp({ view: 'recent' });
  stubBrowserBits(app);
  ctx._notesPrefsCache.defaultView = 'favorites';

  await app.init();

  assert.equal(app.activeView, 'recent');
});
