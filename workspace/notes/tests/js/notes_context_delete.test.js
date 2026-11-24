'use strict';

// Deleting a note from its context menu follows "Confirm before delete" like
// the delete button of the editor does.

const assert = require('node:assert');
const { test } = require('node:test');

const { loadScript } = require('../../../common/tests/js/loader');

function notesWith(prefs) {
  const confirms = [];
  const ctx = loadScript('workspace/notes/ui/static/notes/ui/js/notes.js', {
    fetch: () => Promise.resolve({ ok: false }),
    sidebarPreference: { initial: () => false, save: () => {} },
    addEventListener: () => {},
    document: {
      getElementById: (id) =>
        id === 'notes-prefs-data' ? { textContent: JSON.stringify(prefs) } : null,
    },
    tagsMixin: () => ({}),
    viewerPanelMixin: () => ({}),
    URL,
    location: new URL('http://testserver/notes'),
    navHistory: { push: () => {}, replace: () => {}, returnTo: () => false },
    AppDialog: { confirm: async () => { confirms.push(true); return true; } },
  });
  const app = ctx.notesApp();
  const trashed = [];
  app._fileOperation = async (op, uuid) => { trashed.push([op, uuid]); return true; };
  app.notes = [{ uuid: 'n1', name: 'Plan.md' }];
  app.ctxMenu = { data: { uuid: 'n1', name: 'Plan.md' } };
  app.closeCtxMenu = () => {};
  return { app, confirms, trashed };
}

test('the context menu asks first by default', async () => {
  const { app, confirms, trashed } = notesWith({});
  await app.ctxNoteAction({ id: 'delete' });
  assert.equal(confirms.length, 1);
  assert.equal(trashed.length, 1);
});

test('the context menu trashes at once when confirming is turned off', async () => {
  const { app, confirms, trashed } = notesWith({ confirmBeforeDelete: false });
  await app.ctxNoteAction({ id: 'delete' });
  assert.equal(confirms.length, 0);
  assert.deepStrictEqual(trashed.map((t) => Array.from(t)), [['trash', 'n1']]);
  assert.deepStrictEqual(app.notes, []);
});
