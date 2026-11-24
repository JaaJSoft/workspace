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

function failed() {
  return Promise.resolve({ ok: false, status: 503, headers: { get: () => null } });
}

test('today missing from the page is looked up by name, then created', async () => {
  const { app, requests } = makeApp([page(['2026-11-23', '2026-11-22'], true), page([], false), created(TODAY)]);

  await app.openJournal();

  assert.deepEqual(requests.map((r) => r[0]), ['GET', 'GET', 'POST']);
  assert.match(requests[1][1], /[?&]name=2026-11-24\.md(&|$)/);
  assert.match(requests[1][1], /[?&]parent=22222222-2222-2222-2222-222222222222/);
  assert.deepEqual(app.selected, [TODAY + '.md']);
});

test('a note the page does not show is found by its name, not created again', async () => {
  const { app, requests } = makeApp([page(['ideas', 'archive'], true), page([TODAY], false)]);

  await app.openJournal();

  assert.deepEqual(requests.map((r) => r[0]), ['GET', 'GET']);
  assert.deepEqual(app.selected, [TODAY + '.md']);
});

test('the lookup ignores the filters the list is narrowed by', async () => {
  const { app, requests } = makeApp([page(['2026-11-23'], false), page([TODAY], false)]);
  app.activeView = 'journal';
  app.filters.favorites = true;

  await app.openJournal();

  assert.match(requests[0][1], /[?&]favorites=1/);
  assert.doesNotMatch(requests[1][1], /favorites/);
  assert.deepEqual(requests.map((r) => r[0]), ['GET', 'GET']);
  assert.deepEqual(app.selected, [TODAY + '.md']);
});

test('a failed lookup creates nothing', async () => {
  const { app, requests } = makeApp([page(['2026-11-23'], true), failed()]);

  await app.openJournal();

  assert.deepEqual(requests.map((r) => r[0]), ['GET', 'GET']);
  assert.deepEqual(app.selected, []);
});

test('a journal left during the lookup creates and selects nothing', async () => {
  let answer;
  const pending = new Promise((resolve) => { answer = resolve; });
  const { app, requests } = makeApp([page(['2026-11-23'], true), pending]);

  const opening = app.openJournal();
  await new Promise((resolve) => setImmediate(resolve));
  app.activeView = 'recent';
  answer(page([], false));
  await opening;

  assert.deepEqual(requests.map((r) => r[0]), ['GET', 'GET']);
  assert.deepEqual(app.selected, []);
});
