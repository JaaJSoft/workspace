'use strict';

// Opening a note from the list adds a history entry, switching notes rewrites
// it, closing steps back onto the list through navHistory.returnTo, and a back
// or forward (nav:restore) rebuilds the screen from the URL without writing
// any history itself.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const NOTE_A = { uuid: '0190d5a0-0000-7000-8000-00000000000a', name: 'A.md' };
const NOTE_B = { uuid: '0190d5a0-0000-7000-8000-00000000000b', name: 'B.md' };

function makeApp({ url = '/notes', prefs = {}, config = {} } = {}) {
  const writes = [];
  const fetched = [];
  const known = { [NOTE_A.uuid]: NOTE_A, [NOTE_B.uuid]: NOTE_B };
  let ctx;
  const navigate = (next) => { ctx.location = new URL(next, 'http://testserver'); };

  ctx = loadScript('workspace/notes/ui/static/notes/ui/js/notes.js', {
    URL,
    URLSearchParams,
    location: new URL(url, 'http://testserver'),
    fetch: (requested) => {
      fetched.push(requested);
      const note = known[requested.split('/').pop()];
      return Promise.resolve(note ? { ok: true, json: async () => ({ ...note }) } : { ok: false });
    },
    isValidUuid: () => true,
    sidebarPreference: { initial: () => false, save: () => {} },
    addEventListener: () => {},
    document: {
      getElementById: (id) =>
        id === 'notes-prefs-data' ? { textContent: JSON.stringify(prefs) } : null,
      querySelector: () => null,
      activeElement: null,
    },
    tagsMixin: () => ({}),
    viewerPanelMixin: () => ({}),
    navHistory: {
      push: (next) => { writes.push(['push', next]); navigate(next); },
      replace: (next) => { writes.push(['replace', next]); navigate(next); },
      returnTo: (next) => { writes.push(['returnTo', next]); navigate(next); return false; },
    },
  });

  const app = ctx.notesApp(config);
  app.$nextTick = async () => {};
  app.loadViewerPanel = async () => true;
  app._fetchActionsForSelected = () => {};
  app.setViewCalls = [];
  app.setView = async function (view, id, name, skipUrl) {
    app.setViewCalls.push({ view, id, skipUrl });
    this.activeView = view;
    this.activeId = id || null;
  };
  return { app, ctx, writes, fetched, navigate };
}

function restoreEvent(stale = false) {
  const event = { detail: { state: {}, stale }, prevented: false };
  event.preventDefault = () => { event.prevented = true; };
  return event;
}

test('opening a note from the list pushes, switching to another replaces', async () => {
  const { app, writes } = makeApp();

  await app.selectNote(NOTE_A);
  await app.selectNote(NOTE_B);

  assert.deepStrictEqual(writes, [
    ['push', '/notes?file=' + NOTE_A.uuid],
    ['replace', '/notes?file=' + NOTE_B.uuid],
  ]);
});

test('closing a note returns to the list instead of stacking an entry', async () => {
  const { app, writes } = makeApp();
  await app.selectNote(NOTE_A);

  app.closeNote();

  assert.equal(app.selectedNote, null);
  assert.deepStrictEqual(writes.at(-1), ['returnTo', '/notes']);
  assert.equal(writes.filter((w) => w[0] === 'push').length, 1);
});

test('Escape closes the note through the same path as the back arrow', async () => {
  const { app, writes } = makeApp();
  await app.selectNote(NOTE_A);

  app._handleShortcut({ key: 'Escape', preventDefault() {} });

  assert.equal(app.selectedNote, null);
  assert.deepStrictEqual(writes.at(-1), ['returnTo', '/notes']);
});

test('a deep link opens its note without pushing an entry', async () => {
  const { app, writes } = makeApp({
    url: '/notes?file=' + NOTE_A.uuid,
    config: { view: 'all', file: NOTE_A.uuid },
  });
  app._loadFolderData = () => {};
  app.loadTags = async () => {};
  app._restoreExpandedFolders = async () => {};
  app._setupNotesObserver = () => {};

  await app.init();

  assert.equal(app.selectedNote.uuid, NOTE_A.uuid);
  assert.ok(writes.length > 0);
  assert.ok(writes.every((w) => w[0] === 'replace'), JSON.stringify(writes));
});

test('a restore onto the list closes the note and writes no history', async () => {
  const { app, writes, navigate } = makeApp();
  await app.selectNote(NOTE_A);
  writes.length = 0;
  navigate('/notes');

  await app.onRestore(restoreEvent());

  assert.equal(app.selectedNote, null);
  assert.deepStrictEqual(writes, []);
});

test('a restore onto a note opens it and writes no history', async () => {
  const { app, writes, navigate } = makeApp();
  navigate('/notes?file=' + NOTE_B.uuid);

  await app.onRestore(restoreEvent());

  assert.equal(app.selectedNote.uuid, NOTE_B.uuid);
  assert.deepStrictEqual(writes, []);
});

test('a restore follows the view of the entry without writing it', async () => {
  const { app, writes, navigate } = makeApp();
  const folder = '0190d5a0-0000-7000-8000-0000000000f0';
  navigate('/notes?view=folder&folder=' + folder);

  await app.onRestore(restoreEvent());

  assert.deepStrictEqual(app.setViewCalls, [{ view: 'folder', id: folder, skipUrl: true }]);
  assert.deepStrictEqual(writes, []);
});

test('a stale entry is rebuilt in place rather than reloaded', async () => {
  const { app } = makeApp();
  const event = restoreEvent(true);

  await app.onRestore(event);

  assert.equal(event.prevented, true);
});

test('an entry left without a view reads as the default view', async () => {
  const { app, writes } = makeApp({ prefs: { defaultView: 'favorites' }, config: { view: 'all' } });

  await app.selectNote(NOTE_A);

  assert.deepStrictEqual(writes, [['push', '/notes?view=all&file=' + NOTE_A.uuid]]);
});
