'use strict';

// The notes list pages through /api/v1/files with ?limit=&offset=. The
// endpoint answers a bare array, so "is there another page" comes from the
// X-Has-More header rather than from the payload's shape.

const assert = require('node:assert');
const { test } = require('node:test');

const { loadScript } = require('../../../common/tests/js/loader');

const NOTES_ROOT = '11111111-1111-1111-1111-111111111111';

function loadNotes(fetchStub) {
  return loadScript('workspace/notes/ui/static/notes/ui/js/notes.js', {
    fetch: fetchStub || (() => Promise.resolve({ ok: false })),
    localStorage: { getItem: () => null, setItem: () => {} },
    addEventListener: () => {},
    document: { getElementById: () => null },
    tagsMixin: () => ({}),
    viewerPanelMixin: () => ({}),
  });
}

function makeApp(ctx, fetchStub) {
  const app = ctx.notesApp({});
  app._loadFolderData = () => {};
  app.loadTags = async () => {};
  app._restoreExpandedFolders = async () => {};
  app.refreshSidebar = () => {};
  app.$nextTick = () => {};
  app.notePrefs = { defaultFolderUuid: NOTES_ROOT, journalFolderUuid: null };
  if (fetchStub) ctx.fetch = fetchStub;
  return app;
}

// A bare-array response carrying the pagination header.
function page(names, hasMore) {
  return Promise.resolve({
    ok: true,
    headers: { get: (h) => (h.toLowerCase() === 'x-has-more' ? String(hasMore) : null) },
    json: () => Promise.resolve(names.map((n) => ({ uuid: n, name: n + '.md' }))),
  });
}

test('the list URL asks for a bounded page instead of the whole folder', () => {
  const ctx = loadNotes();
  const app = makeApp(ctx);
  app.activeView = 'all';

  const url = app._buildNotesUrl();

  assert.match(url, /[?&]limit=\d+/, 'no limit means the endpoint returns everything');
  assert.match(url, /[?&]offset=0/);
  assert.ok(!url.includes('recent_limit'), 'recent_limit is superseded by limit');
});

test('every list view is paginated, not just My Notes', () => {
  const ctx = loadNotes();
  const app = makeApp(ctx);

  for (const view of ['all', 'recent', 'favorites', 'tag', 'folder']) {
    app.activeView = view;
    app.activeId = view === 'all' ? null : NOTES_ROOT;
    assert.match(app._buildNotesUrl(), /[?&]limit=\d+/, `${view} view is unbounded`);
  }
});

test('the next page is requested at the offset the list has reached', () => {
  const ctx = loadNotes();
  const app = makeApp(ctx);
  app.activeView = 'all';

  assert.match(app._buildNotesUrl(250), /[?&]offset=250/);
});

test('a fresh load replaces the list and an append extends it', async () => {
  const ctx = loadNotes();
  const app = makeApp(ctx);
  ctx.fetch = () => page(['a', 'b'], true);

  await app.loadNotes('/api/v1/files?limit=2&offset=0');
  assert.deepEqual(app.notes.map((n) => n.uuid), ['a', 'b']);

  ctx.fetch = () => page(['c', 'd'], false);
  await app.loadNotes('/api/v1/files?limit=2&offset=2', { append: true });
  assert.deepEqual(app.notes.map((n) => n.uuid), ['a', 'b', 'c', 'd']);
});

test('hasMoreNotes follows the X-Has-More header', async () => {
  const ctx = loadNotes();
  const app = makeApp(ctx);

  ctx.fetch = () => page(['a'], true);
  await app.loadNotes('/api/v1/files?limit=1');
  assert.equal(app.hasMoreNotes, true);

  ctx.fetch = () => page(['b'], false);
  await app.loadNotes('/api/v1/files?limit=1');
  assert.equal(app.hasMoreNotes, false);
});

test('an appended page never duplicates a note already on screen', async () => {
  const ctx = loadNotes();
  const app = makeApp(ctx);

  ctx.fetch = () => page(['a', 'b'], true);
  await app.loadNotes('/api/v1/files?limit=2&offset=0');

  // A note created between two fetches shifts the window, so the next page
  // legitimately re-sends a row the list already holds.
  ctx.fetch = () => page(['b', 'c'], false);
  await app.loadNotes('/api/v1/files?limit=2&offset=2', { append: true });

  assert.deepEqual(app.notes.map((n) => n.uuid), ['a', 'b', 'c']);
});

test('loadMoreNotes stops once the server says there is nothing left', async () => {
  const ctx = loadNotes();
  const app = makeApp(ctx);
  let calls = 0;
  ctx.fetch = () => { calls += 1; return page(['a'], false); };

  await app.loadNotes('/api/v1/files?limit=1');
  assert.equal(calls, 1);

  await app.loadMoreNotes();
  assert.equal(calls, 1, 'a request went out with hasMoreNotes false');
});

test('loadMoreNotes does not fire a second request while one is in flight', async () => {
  const ctx = loadNotes();
  const app = makeApp(ctx);
  app.activeView = 'all';
  let calls = 0;
  let release;
  ctx.fetch = () => {
    calls += 1;
    return new Promise((resolve) => { release = () => resolve(page(['x'], true)); });
  };

  app.hasMoreNotes = true;
  const first = app.loadMoreNotes();
  app.loadMoreNotes();
  assert.equal(calls, 1, 'concurrent scroll events each fired a request');
  release();
  await first;
});

test('a page from the previous view never lands in the current one', async () => {
  const ctx = loadNotes();
  const app = makeApp(ctx);
  app.activeView = 'all';

  let release;
  ctx.fetch = () => new Promise((resolve) => {
    release = () => resolve(page(['stale'], true));
  });
  const pending = app.loadNotes('/api/v1/files?limit=2&offset=0');

  // The user switches folder while that request is still open.
  ctx.fetch = () => page(['fresh'], false);
  await app.loadNotes('/api/v1/files?limit=2&offset=0');

  release();
  await pending;

  assert.deepEqual(app.notes.map((n) => n.uuid), ['fresh']);
});

test('resync reloads every page the user had scrolled through', async () => {
  const ctx = loadNotes();
  const app = makeApp(ctx);
  app.activeView = 'all';
  app.refreshSidebar = () => {};

  ctx.fetch = () => page(['a', 'b'], true);
  await app.loadNotes(app._buildNotesUrl());
  ctx.fetch = () => page(['c', 'd'], false);
  await app.loadMoreNotes();
  assert.equal(app.notes.length, 4);

  // The stream came back: the list must not shrink to a single page.
  const responses = [page(['a', 'b'], true), page(['c', 'd'], false)];
  ctx.fetch = () => responses.shift();
  await app.resync();

  assert.equal(app.notes.length, 4);
});

test('resync gives up instead of spinning when a page fails to load', async () => {
  const ctx = loadNotes();
  const app = makeApp(ctx);
  app.activeView = 'all';
  app.refreshSidebar = () => {};

  ctx.fetch = () => page(['a', 'b'], true);
  await app.loadNotes(app._buildNotesUrl());
  ctx.fetch = () => page(['c', 'd'], true);
  await app.loadMoreNotes();

  // First page succeeds and still reports more, every follow-up fails - so
  // hasMoreNotes stays true while the list stops growing.
  let calls = 0;
  ctx.fetch = () => {
    calls += 1;
    return calls === 1 ? page(['a', 'b'], true) : Promise.resolve({ ok: false });
  };

  await app.resync();

  assert.ok(calls < 10, `resync looped ${calls} times on a failing page`);
});
