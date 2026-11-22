'use strict';

// The agenda view pages through /api/v1/events with a cursor (next_after +
// next_after_id). These tests drive the real mixin against a fake fetch.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

function makeApp() {
  const ctx = loadScripts([
    'workspace/common/static/ui/js/zoned_formatter.js',
    'workspace/calendar/ui/static/calendar/ui/js/calendar_events.js',
  ], {
    document: { getElementById: () => null },
    URLSearchParams,
  });
  const app = ctx.calendarEventsMixin();
  Object.assign(app, {
    currentView: 'agenda',
    visibleCalendars: { cal: true },
    prefs: { showDeclined: false },
    agenda: {
      events: [],
      nextAfter: null,
      nextAfterId: null,
      loading: false,
      initialLoaded: false,
      seenIds: new Set(),
    },
  });
  return { ctx, app };
}

// One page of the cursor endpoint; `next` null means the list is exhausted.
function page(uuids, next) {
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve({
      events: uuids.map((uuid) => ({ uuid })),
      next_after: next,
      next_after_id: next && `${uuids.at(-1)}:0`,
    }),
  });
}

function uuids(app) {
  return app.agenda.events.map((e) => e.uuid);
}

// Three pages loaded by scrolling, as a reader a few weeks ahead has them.
async function scrolledAgenda() {
  const { ctx, app } = makeApp();
  const pages = [page(['a', 'b'], 't1'), page(['c', 'd'], 't2'), page(['e', 'f'], 't3')];
  ctx.fetch = () => pages.shift();
  await app.loadAgenda();
  await app.loadMoreAgenda();
  await app.loadMoreAgenda();
  return { ctx, app };
}

test('the agenda opens on one page and loading more follows the cursor', async () => {
  const { ctx, app } = makeApp();
  const urls = [];
  const pages = [page(['a', 'b'], 't1'), page(['b', 'c'], null)];
  ctx.fetch = (url) => { urls.push(new URL(url, 'http://x')); return pages.shift(); };

  await app.loadAgenda();
  assert.ok(app.agenda.initialLoaded);
  assert.deepEqual(uuids(app), ['a', 'b']);

  await app.loadMoreAgenda();
  assert.deepEqual(uuids(app), ['a', 'b', 'c'], 'a boundary event shows once');
  assert.equal(app.agenda.nextAfter, null);
  assert.equal(urls[1].searchParams.get('after'), 't1');
  assert.equal(urls[1].searchParams.get('after_id'), 'b:0');

  await app.loadMoreAgenda();
  assert.equal(urls.length, 2, 'an exhausted agenda asks for nothing more');
});

test('a refresh keeps the loaded events on screen until the new ones replace them', async () => {
  const { ctx, app } = await scrolledAgenda();
  // b ended meanwhile; the page is full, so the cursor goes on.
  const refreshed = [...Array.from({ length: 15 }, (_, i) => `n${i}`), 'c', 'd', 'e', 'f', 'g'];

  // Snapshot the list at every request: an empty list for one frame is what
  // scrolled the reader back to today.
  const seen = [];
  const urls = [];
  ctx.fetch = (url) => {
    seen.push(uuids(app).join(','));
    urls.push(new URL(url, 'http://x'));
    return page(refreshed, 't4');
  };
  await app.refreshAgenda();

  assert.deepEqual(seen, ['a,b,c,d,e,f'], 'the list was cleared while it reloaded');
  assert.equal(urls[0].searchParams.get('limit'), '20', 'one request covers what was loaded');
  assert.equal(urls[0].searchParams.get('after_id'), null, 'the refresh starts from now');
  assert.deepEqual(uuids(app), refreshed);
  assert.equal(app.agenda.nextAfter, 't4');
  assert.equal(app.agenda.nextAfterId, 'g:0');
  assert.ok(app.agenda.initialLoaded, 'a silent refresh must not show the spinner');
  assert.equal(app.agenda.loading, false);

  ctx.fetch = () => page(['g', 'h'], null);
  await app.loadMoreAgenda();
  assert.deepEqual(uuids(app), [...refreshed, 'h'], 'scrolling resumes after the refresh');
});

test('a refresh follows the cursor until it holds as many events as before', async () => {
  const { ctx, app } = await scrolledAgenda();

  const urls = [];
  const pages = [page(['a', 'b', 'c'], 't1'), page(['c', 'd', 'e', 'f'], null)];
  ctx.fetch = (url) => { urls.push(new URL(url, 'http://x')); return pages.shift(); };
  await app.refreshAgenda();

  assert.equal(urls.length, 2);
  assert.equal(urls[1].searchParams.get('after'), 't1');
  assert.equal(urls[1].searchParams.get('after_id'), 'c:0');
  assert.deepEqual(uuids(app), ['a', 'b', 'c', 'd', 'e', 'f']);
  assert.equal(app.agenda.nextAfter, null);
});

test('a refresh stops when a page brings nothing new', async () => {
  const { ctx, app } = await scrolledAgenda();

  let requests = 0;
  ctx.fetch = () => { requests++; return page(['a', 'b'], 't1'); };
  await app.refreshAgenda();

  assert.equal(requests, 2);
  assert.deepEqual(uuids(app), ['a', 'b']);
});

test('a refresh whose request fails leaves the agenda as it was', async () => {
  const { ctx, app } = await scrolledAgenda();

  ctx.fetch = () => Promise.resolve({ ok: false });
  await app.refreshAgenda();

  assert.deepEqual(uuids(app), ['a', 'b', 'c', 'd', 'e', 'f']);
  assert.equal(app.agenda.nextAfter, 't3');

  ctx.fetch = () => page(['g'], null);
  await app.loadMoreAgenda();
  assert.deepEqual(uuids(app).at(-1), 'g', 'scrolling still works after a failed refresh');
});

test('a filter change during a refresh wins over it', async () => {
  const { ctx, app } = await scrolledAgenda();

  let release;
  ctx.fetch = () => new Promise((resolve) => {
    release = () => resolve(page(['stale'], null));
  });
  const pending = app.refreshAgenda();

  ctx.fetch = () => page(['other'], null);
  await app.loadAgenda();

  release();
  await pending;

  assert.deepEqual(uuids(app), ['other']);
});

test('loading more waits for a refresh instead of appending to the old list', async () => {
  const { ctx, app } = await scrolledAgenda();

  let release;
  let requests = 0;
  ctx.fetch = () => {
    requests++;
    return new Promise((resolve) => {
      release = () => resolve(page(['a', 'b', 'c', 'd', 'e', 'f'], null));
    });
  };
  const pending = app.refreshAgenda();
  await app.loadMoreAgenda();
  release();
  await pending;

  assert.equal(requests, 1, 'the scroll observer fired a page from the stale cursor');
  assert.deepEqual(uuids(app), ['a', 'b', 'c', 'd', 'e', 'f']);
});

test('a refresh before the first page landed starts the agenda over', async () => {
  const { ctx, app } = makeApp();

  ctx.fetch = () => page(['a'], null);
  await app.refreshAgenda();

  assert.deepEqual(uuids(app), ['a']);
  assert.ok(app.agenda.initialLoaded);
});

test('a refresh outside the agenda view asks for nothing', async () => {
  const { ctx, app } = await scrolledAgenda();
  app.currentView = 'month';

  let requests = 0;
  ctx.fetch = () => { requests++; return page([], null); };
  await app.refreshAgenda();

  assert.equal(requests, 0);
});
