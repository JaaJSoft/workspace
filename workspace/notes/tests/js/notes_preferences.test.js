'use strict';

// The notes page mounts notesPreferences() twice (sidebar popover and
// dialog); both show the configured folders by name.

const assert = require('node:assert');
const { test } = require('node:test');

const { loadScript } = require('../../../common/tests/js/loader');

const NOTES_ROOT = '11111111-1111-1111-1111-111111111111';
const JOURNAL = '22222222-2222-2222-2222-222222222222';

function loadPage(prefs) {
  const listeners = [];
  const requests = [];
  const ctx = loadScript('workspace/notes/ui/static/notes/ui/js/notes.js', {
    fetch: (url) => {
      requests.push(url);
      const uuid = url.split('/').pop();
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ name: uuid === JOURNAL ? 'Journal' : 'Notes', path: null }),
      });
    },
    addEventListener: (type, fn) => listeners.push([type, fn]),
    dispatchEvent: (event) => {
      for (const [type, fn] of listeners) if (type === event.type) fn(event);
    },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    document: {
      getElementById: (id) => (id === 'notes-prefs-data' ? { textContent: JSON.stringify(prefs) } : null),
    },
    getCSRFToken: () => '',
    setTimeout: () => 0,
    clearTimeout: () => {},
  });
  return { ctx, requests };
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

test('the two panels of the page fetch each folder name once', async () => {
  const { ctx, requests } = loadPage({ defaultFolderUuid: NOTES_ROOT, journalFolderUuid: JOURNAL });
  const popover = ctx.notesPreferences();
  const dialog = ctx.notesPreferences();

  await Promise.all([popover.init(), dialog.init()]);

  assert.equal(requests.length, 2);
  assert.equal(popover.defaultFolderName, 'Notes');
  assert.equal(dialog.journalFolderName, 'Journal');
});

test('toggling a preference fetches no folder name again', async () => {
  const { ctx, requests } = loadPage({ defaultFolderUuid: NOTES_ROOT, journalFolderUuid: JOURNAL });
  const popover = ctx.notesPreferences();
  const dialog = ctx.notesPreferences();
  await Promise.all([popover.init(), dialog.init()]);
  requests.length = 0;

  popover.update('showTags', false);
  await flush();

  assert.deepEqual(requests, []);
  assert.equal(dialog.prefs.showTags, false);
  assert.equal(dialog.defaultFolderName, 'Notes');
});
