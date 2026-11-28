'use strict';

// The agenda view pages through GET /api/v1/events with `limit` and
// `offset`, every page of one reading naming the same `start`. These tests
// drive the real mixin against a fake fetch.

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
    visibleCalendars: { cal: true, hidden: false },
    prefs: { showDeclined: false },
    agenda: {
      events: [],
      start: null,
      offset: 0,
      hasMore: false,
      loading: false,
      initialLoaded: false,
      seenIds: new Set(),
    },
  });
  return { ctx, app };
}

// One page of the list: a bare array, X-Has-More saying whether a row follows.
function page(uuids, hasMore) {
  return Promise.resolve({
    ok: true,
    headers: { get: (name) => (name === 'X-Has-More' ? String(hasMore) : null) },
    json: () => Promise.resolve(uuids.map((uuid) => ({ uuid, original_start: null }))),
  });
}

function uuids(app) {
  return app.agenda.events.map((e) => e.uuid);
}

function params(url) {
  return new URL(url, 'http://x').searchParams;
}

// Three pages loaded by scrolling, as a reader a few weeks ahead has them.
async function scrolledAgenda() {
  const { ctx, app } = makeApp();
  const pages = [page(['a', 'b'], true), page(['c', 'd'], true), page(['e', 'f'], true)];
  ctx.fetch = () => pages.shift();
  await app.loadAgenda();
  await app.loadMoreAgenda();
  await app.loadMoreAgenda();
  return { ctx, app };
}

test('the agenda opens on one page and loading more moves the offset', async () => {
  const { ctx, app } = makeApp();
  const asked = [];
  const pages = [page(['a', 'b'], true), page(['b', 'c'], false)];
  ctx.fetch = (url) => { asked.push(params(url)); return pages.shift(); };

  await app.loadAgenda();
  assert.ok(app.agenda.initialLoaded);
  assert.deepEqual(uuids(app), ['a', 'b']);
  assert.equal(asked[0].get('offset'), '0');
  assert.equal(asked[0].get('limit'), '20');

  await app.loadMoreAgenda();
  assert.deepEqual(uuids(app), ['a', 'b', 'c'], 'a row shifted onto the next page shows once');
  assert.equal(app.agenda.hasMore, false);
  assert.equal(asked[1].get('offset'), '2');
  assert.equal(asked[1].get('start'), asked[0].get('start'), 'the offset counts from one start');

  await app.loadMoreAgenda();
  assert.equal(asked.length, 2, 'an exhausted agenda asks for nothing more');
});

test('the agenda asks for the ticked calendars and the invitations', async () => {
  const { ctx, app } = makeApp();
  let asked;
  ctx.fetch = (url) => { asked = params(url); return page([], false); };

  await app.loadAgenda();

  // `none` stands for the events the viewer is only invited to: they sit
  // in no calendar of theirs, and the grid lists them too.
  assert.equal(asked.get('calendar'), 'cal,none');
  assert.equal(asked.get('include_declined'), 'false');
  assert.equal(asked.get('end'), null, 'the agenda has no end');
});

test('two occurrences of one series are two rows', async () => {
  const { ctx, app } = makeApp();
  ctx.fetch = () => Promise.resolve({
    ok: true,
    headers: { get: () => 'false' },
    json: () => Promise.resolve([
      { uuid: 's', original_start: '2026-03-02T09:00:00Z' },
      { uuid: 's', original_start: '2026-03-09T09:00:00Z' },
    ]),
  });

  await app.loadAgenda();

  assert.equal(app.agenda.events.length, 2);
  assert.notEqual(
    app._occurrenceKey(app.agenda.events[0]),
    app._occurrenceKey(app.agenda.events[1]),
  );
});

test('a refresh keeps the loaded events on screen until the new ones replace them', async () => {
  const { ctx, app } = await scrolledAgenda();
  // b ended meanwhile; the page is full, so the list goes on.
  const refreshed = [...Array.from({ length: 15 }, (_, i) => `n${i}`), 'c', 'd', 'e', 'f', 'g'];

  // Snapshot the list at every request: an empty list for one frame is what
  // scrolled the reader back to today.
  const seen = [];
  const asked = [];
  ctx.fetch = (url) => {
    seen.push(uuids(app).join(','));
    asked.push(params(url));
    return page(refreshed, true);
  };
  await app.refreshAgenda();

  assert.deepEqual(seen, ['a,b,c,d,e,f'], 'the list was cleared while it reloaded');
  assert.equal(asked[0].get('limit'), '20', 'one request covers what was loaded');
  assert.equal(asked[0].get('offset'), '0', 'the refresh starts from now');
  assert.deepEqual(uuids(app), refreshed);
  assert.equal(app.agenda.offset, 20);
  assert.equal(app.agenda.hasMore, true);
  assert.equal(app.agenda.start, asked[0].get('start'));
  assert.ok(app.agenda.initialLoaded, 'a silent refresh must not show the spinner');
  assert.equal(app.agenda.loading, false);

  ctx.fetch = (url) => { asked.push(params(url)); return page(['g', 'h'], false); };
  await app.loadMoreAgenda();
  assert.deepEqual(uuids(app), [...refreshed, 'h'], 'scrolling resumes after the refresh');
  assert.equal(asked[1].get('offset'), '20');
  assert.equal(asked[1].get('start'), asked[0].get('start'));
});

test('a refresh follows the pages until it holds as many events as before', async () => {
  const { ctx, app } = await scrolledAgenda();

  const asked = [];
  const pages = [page(['a', 'b', 'c'], true), page(['c', 'd', 'e', 'f'], false)];
  ctx.fetch = (url) => { asked.push(params(url)); return pages.shift(); };
  await app.refreshAgenda();

  assert.equal(asked.length, 2);
  assert.equal(asked[1].get('offset'), '3');
  assert.equal(asked[1].get('start'), asked[0].get('start'));
  assert.deepEqual(uuids(app), ['a', 'b', 'c', 'd', 'e', 'f']);
  assert.equal(app.agenda.hasMore, false);
});

test('a refresh stops when a page brings nothing new', async () => {
  const { ctx, app } = await scrolledAgenda();

  let requests = 0;
  ctx.fetch = () => { requests++; return page(['a', 'b'], true); };
  await app.refreshAgenda();

  assert.equal(requests, 2);
  assert.deepEqual(uuids(app), ['a', 'b']);
});

test('a refresh whose request fails leaves the agenda as it was', async () => {
  const { ctx, app } = await scrolledAgenda();
  const start = app.agenda.start;

  ctx.fetch = () => Promise.resolve({ ok: false });
  await app.refreshAgenda();

  assert.deepEqual(uuids(app), ['a', 'b', 'c', 'd', 'e', 'f']);
  assert.equal(app.agenda.offset, 6);
  assert.equal(app.agenda.start, start);

  ctx.fetch = () => page(['g'], false);
  await app.loadMoreAgenda();
  assert.deepEqual(uuids(app).at(-1), 'g', 'scrolling still works after a failed refresh');
});

test('a filter change during a refresh wins over it', async () => {
  const { ctx, app } = await scrolledAgenda();

  let release;
  ctx.fetch = () => new Promise((resolve) => {
    release = () => resolve(page(['stale'], false));
  });
  const pending = app.refreshAgenda();

  ctx.fetch = () => page(['other'], false);
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
      release = () => resolve(page(['a', 'b', 'c', 'd', 'e', 'f'], false));
    });
  };
  const pending = app.refreshAgenda();
  await app.loadMoreAgenda();
  release();
  await pending;

  assert.equal(requests, 1, 'the scroll observer fired a page from the stale offset');
  assert.deepEqual(uuids(app), ['a', 'b', 'c', 'd', 'e', 'f']);
});

test('a refresh before the first page landed starts the agenda over', async () => {
  const { ctx, app } = makeApp();

  ctx.fetch = () => page(['a'], false);
  await app.refreshAgenda();

  assert.deepEqual(uuids(app), ['a']);
  assert.ok(app.agenda.initialLoaded);
});

test('a refresh outside the agenda view asks for nothing', async () => {
  const { ctx, app } = await scrolledAgenda();
  app.currentView = 'month';

  let requests = 0;
  ctx.fetch = () => { requests++; return page([], false); };
  await app.refreshAgenda();

  assert.equal(requests, 0);
});

test('a window of the grid is read whole, page after page', async () => {
  const { ctx, app } = makeApp();
  ctx.document.body = { dataset: { userId: '1' } };
  const event = (uuid) => ({
    uuid,
    original_start: null,
    title: uuid,
    start: '2026-03-02T09:00:00Z',
    end: null,
    is_all_day: false,
    calendar: { uuid: 'cal', name: 'Work', color: 'accent' },
    owner: { id: 1 },
    members: [],
    guests: [],
  });
  const asked = [];
  const pages = [
    { events: [event('a'), event('b')], more: 'true' },
    { events: [event('c')], more: 'false' },
  ];
  ctx.fetch = (url) => {
    asked.push(params(url));
    const next = pages.shift();
    return Promise.resolve({
      ok: true,
      headers: { get: () => next.more },
      json: () => Promise.resolve(next.events),
    });
  };

  const drawn = await app.fetchEvents('2026-03-01T00:00:00+01:00', '2026-04-01T00:00:00+02:00');

  assert.deepEqual(Array.from(drawn, (e) => e.id), ['a', 'b', 'c']);
  assert.equal(asked.length, 2);
  assert.equal(asked[0].get('start'), '2026-03-01T00:00:00+01:00', 'the offset survives the query string');
  assert.equal(asked[0].get('calendar'), 'cal,none');
  assert.equal(asked[1].get('offset'), asked[0].get('limit'));
  assert.deepEqual(Array.from(drawn[0].classNames), ['event-color-accent']);
});
