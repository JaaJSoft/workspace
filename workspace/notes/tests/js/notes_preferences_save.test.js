'use strict';

// Both write paths of the notes preferences put the whole object under
// notes/preferences, and neither lets a failed write escape.
const assert = require('node:assert/strict');
const { test } = require('node:test');

const { loadScripts } = require('../../../common/tests/js/loader');

function loadNotes(reply = { ok: true, status: 200 }) {
  const puts = [];
  const ctx = loadScripts([
    'workspace/core/static/core/js/user_settings.js',
    'workspace/notes/ui/static/notes/ui/js/notes.js',
  ], {
    getCSRFToken: () => 'csrf-token',
    setTimeout: (fn) => fn(),
    clearTimeout: () => {},
    fetch: (url, options = {}) => {
      if (options.method === 'PUT') puts.push({ url, body: JSON.parse(options.body) });
      return reply instanceof Error ? Promise.reject(reply) : Promise.resolve(reply);
    },
    sidebarPreference: { initial: () => false, save: () => {} },
    addEventListener: () => {},
    dispatchEvent: () => {},
    CustomEvent: class {
      constructor(type, init) {
        this.type = type;
        this.detail = init?.detail;
      }
    },
    document: { getElementById: () => null },
    tagsMixin: () => ({}),
    viewerPanelMixin: () => ({}),
    URL,
    location: new URL('http://testserver/notes'),
    navHistory: { push: () => {}, replace: () => {}, returnTo: () => false },
  });
  return { ctx, puts };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

test('the preferences popover puts the whole object once the debounce runs', () => {
  const { ctx, puts } = loadNotes();
  ctx.notesPreferences().update('showTags', false);

  assert.equal(puts.length, 1);
  assert.equal(puts[0].url, '/api/v1/settings/notes/preferences');
  assert.equal(puts[0].body.value.showTags, false);
  assert.equal(puts[0].body.value.showFolders, true);
});

test('a preference changed from the page puts the whole cache', () => {
  const { ctx, puts } = loadNotes();
  const app = ctx.notesApp({});
  app._updatePref('showFolders', false);

  assert.equal(puts.length, 1);
  assert.equal(puts[0].url, '/api/v1/settings/notes/preferences');
  assert.equal(puts[0].body.value.showFolders, false);
});

test('a refused or lost write is swallowed', async () => {
  for (const reply of [{ ok: false, status: 400 }, new TypeError('offline')]) {
    const { ctx } = loadNotes(reply);
    ctx.notesPreferences().update('showTags', false);
    ctx.notesApp({})._updatePref('showFolders', false);
    await settle();
  }
});
