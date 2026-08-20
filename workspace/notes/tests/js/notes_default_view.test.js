'use strict';

// The notes preferences are embedded server-side via |json_script and read
// synchronously when notes.js loads, so notesApp resolves its initial view
// in the factory - the first Alpine paint must already reflect a saved
// defaultView preference, with the URL view taking priority.

const assert = require('node:assert');
const { test } = require('node:test');

const { loadScript } = require('../../../common/tests/js/loader');

function loadNotes(embeddedPrefs) {
  return loadScript('workspace/notes/ui/static/notes/ui/js/notes.js', {
    fetch: () => Promise.resolve({ ok: false }),
    localStorage: { getItem: () => null, setItem: () => {} },
    addEventListener: () => {},
    document: {
      getElementById: (id) =>
        id === 'notes-prefs-data' && embeddedPrefs
          ? { textContent: JSON.stringify(embeddedPrefs) }
          : null,
    },
    // Defined by files/ui/js/tags.js in the browser; its contents are
    // irrelevant to view resolution.
    tagsMixin: () => ({}),
    viewerPanelMixin: () => ({}),
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

test('the embedded prefs seed the cache over the defaults', () => {
  const ctx = loadNotes({ defaultView: 'favorites', showTags: false });

  assert.equal(ctx._notesPrefsCache.defaultView, 'favorites');
  assert.equal(ctx._notesPrefsCache.showTags, false);
  // Untouched keys keep their defaults.
  assert.equal(ctx._notesPrefsCache.showFolders, true);
});

test('a saved defaultView pref applies from the first paint when the URL has no view', async () => {
  const ctx = loadNotes({ defaultView: 'favorites' });

  const app = ctx.notesApp({});
  stubBrowserBits(app);

  assert.equal(app.activeView, 'favorites');
  assert.equal(app.viewTitle, 'Favorites');

  // init() must not disturb the resolved view.
  await app.init();
  assert.equal(app.activeView, 'favorites');
});

test('the URL view wins over the saved pref', async () => {
  const ctx = loadNotes({ defaultView: 'favorites' });

  const app = ctx.notesApp({ view: 'recent' });
  stubBrowserBits(app);

  await app.init();

  assert.equal(app.activeView, 'recent');
});
