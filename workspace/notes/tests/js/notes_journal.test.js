'use strict';

// The journal pages through its folder like every other list, and still
// finds today's note when one exists.

const assert = require('node:assert');
const { test } = require('node:test');

const { loadScript } = require('../../../common/tests/js/loader');

const JOURNAL = '22222222-2222-2222-2222-222222222222';
const TODAY = '2026-11-24';

function makeApp(responses) {
  const requests = [];
  const ctx = loadScript('workspace/notes/ui/static/notes/ui/js/notes.js', {
    fetch: (url, init) => {
      requests.push([init && init.method ? init.method : 'GET', url]);
      return responses.shift();
    },
    sidebarPreference: { initial: () => false, save: () => {} },
    addEventListener: () => {},
    document: { getElementById: () => null },
    tagsMixin: () => ({}),
    viewerPanelMixin: () => ({}),
    userTzDayKey: () => TODAY,
    FormData: class { append() {} },
    Blob: class {},
    getCSRFToken: () => '',
  });
  const app = ctx.notesApp({});
  app.notePrefs = { defaultFolderUuid: null, journalFolderUuid: JOURNAL };
  app._closeDrawerOnMobile = () => {};
  app.updateUrl = () => {};
  app.selected = [];
  app.selectNote = async (note) => { app.selected.push(note.name); };
  return { app, requests };
}

function page(names, hasMore) {
  return Promise.resolve({
    ok: true,
    headers: { get: (h) => (h.toLowerCase() === 'x-has-more' ? String(hasMore) : null) },
    json: () => Promise.resolve(names.map((n) => ({ uuid: n, name: n + '.md' }))),
  });
}

function created(name) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve({ uuid: name, name: name + '.md' }) });
}

test('the journal asks for one page, not the whole folder', async () => {
  const { app, requests } = makeApp([page([TODAY, '2026-11-23'], true)]);

  await app.openJournal();

  assert.equal(requests.length, 1);
  assert.match(requests[0][1], /[?&]parent=22222222-2222-2222-2222-222222222222/);
  assert.match(requests[0][1], /[?&]limit=\d+/);
  assert.deepEqual(app.selected, [TODAY + '.md']);
});

test('a first page ending below today means today has no note yet', async () => {
  const { app, requests } = makeApp([page(['2026-11-23', '2026-11-22'], true), created(TODAY)]);

  await app.openJournal();

  assert.deepEqual(requests.map((r) => r[0]), ['GET', 'POST']);
  assert.deepEqual(app.selected, [TODAY + '.md']);
});

test('names sorting above today push its note to a later page, which is fetched', async () => {
  const { app, requests } = makeApp([
    page(['ideas', 'archive'], true),
    page([TODAY, '2026-11-23'], true),
  ]);

  await app.openJournal();

  assert.deepEqual(requests.map((r) => r[0]), ['GET', 'GET']);
  assert.deepEqual(app.selected, [TODAY + '.md']);
});
