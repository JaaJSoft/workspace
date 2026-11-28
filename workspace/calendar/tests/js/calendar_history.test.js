'use strict';

// The calendar writes browser history only through navHistory: opening an
// event pushes, everything else rewrites the entry, closing steps back onto
// the entry it was opened from, and a back or forward rebuilds the screen
// from the URL without writing anything.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');
const { sidebarPreferenceStub } = require('../../../core/tests/js/sidebar_preference_stub');

const EVENT_A = '0190a2b4-0000-7000-8000-00000000000a';
const EVENT_B = '0190a2b4-0000-7000-8000-00000000000b';

function rawEvent(uuid) {
  return {
    uuid,
    calendar: { uuid: 'c1', name: 'Work', color: 'error' },
    title: 'Standup',
    start: '2026-03-20T09:00:00Z',
    end: '2026-03-20T10:00:00Z',
    is_all_day: false,
    owner: { id: 1 },
    members: [],
  };
}

function makeApp(search = '?view=month') {
  const location = { pathname: '/calendar', search };
  const writes = [];
  const setUrl = (url) => { location.search = new URL(url, 'http://x').search; };
  const navHistory = {
    push(url) { writes.push(['push', url]); setUrl(url); },
    replace(url) { writes.push(['replace', url]); setUrl(url); },
    returnTo(url) { writes.push(['returnTo', url]); setUrl(url); return false; },
  };
  const ctx = loadScripts([
    'workspace/common/static/ui/js/zoned_formatter.js',
    'workspace/common/static/ui/js/uuid.js',
    'workspace/calendar/ui/static/calendar/ui/js/calendar_events.js',
    'workspace/calendar/ui/static/calendar/ui/js/calendar.js',
  ], {
    document: { getElementById: () => null, body: { dataset: { userId: '1' } } },
    localStorage: { getItem: () => null, setItem: () => {} },
    sidebarPreference: sidebarPreferenceStub(),
    matchMedia: () => ({ matches: false, addEventListener: () => {} }),
    calendarCalendarsMixin: () => ({}),
    calendarTasksMixin: () => ({}),
    calendarRecurrenceMixin: () => ({}),
    calendarPollsMixin: () => ({}),
    location,
    navHistory,
    URLSearchParams,
  });
  ctx.getUserTimeZone = () => 'UTC';
  ctx.isoToWallClock = (iso) => iso.slice(0, 16);

  const fc = {
    date: new Date('2026-03-15T00:00:00Z'),
    moves: [],
    view: { title: 'March 2026' },
    getDate() { return this.date; },
    gotoDate(d) { this.moves.push(d); this.date = new Date(d); },
    today() { this.moves.push('today'); this.date = new Date(); },
    changeView(v) { this.moves.push(v); },
    setOption() {},
  };
  const app = ctx.calendarApp();
  app.calendar = fc;
  app.currentView = 'dayGridMonth';
  app.$nextTick = (fn) => fn();
  return { app, ctx, fc, writes, location };
}

function restoreEvent(stale = false) {
  return {
    detail: { state: {}, stale },
    prevented: false,
    preventDefault() { this.prevented = true; },
  };
}

test('opening an event pushes, opening another over it rewrites the entry', () => {
  const { app, writes } = makeApp();

  app.openViewPanel(rawEvent(EVENT_A));
  app.openViewPanel(rawEvent(EVENT_B));

  assert.deepStrictEqual(writes, [
    ['push', `/calendar?view=month&date=2026-03-15&event=${EVENT_A}`],
    ['replace', `/calendar?view=month&date=2026-03-15&event=${EVENT_B}`],
  ]);
});

test('closing the panel returns to the address without the event', () => {
  const { app, writes } = makeApp();
  app.openViewPanel(rawEvent(EVENT_A));

  app.closePanel();

  assert.equal(app.showPanel, false);
  assert.deepStrictEqual(writes.at(-1), ['returnTo', '/calendar?view=month&date=2026-03-15']);
});

test('creating an event over an open panel closes it like the close button', () => {
  const { app, writes } = makeApp();
  app.openViewPanel(rawEvent(EVENT_A));

  app.openCreateModal('2026-03-20', '', true);

  assert.equal(app.showPanel, false);
  assert.equal(app.showModal, true);
  assert.deepStrictEqual(writes.at(-1), ['returnTo', '/calendar?view=month&date=2026-03-15']);
});

test('date navigation rewrites the entry', () => {
  const { app, writes, fc } = makeApp();
  fc.next = () => { fc.date = new Date('2026-04-15T00:00:00Z'); };

  app.calendarNext();

  assert.deepStrictEqual(writes, [['replace', '/calendar?view=month&date=2026-04-15']]);
});

test('a restore rebuilds view, date and panel from the URL and writes nothing', () => {
  const { app, writes, fc, location } = makeApp();
  app.openViewPanel(rawEvent(EVENT_A));
  writes.length = 0;
  location.search = '?view=week&date=2026-03-08';

  const event = restoreEvent();
  app.restoreFromUrl(event);

  assert.equal(app.currentView, 'timeGridWeek');
  assert.deepStrictEqual(fc.moves, ['timeGridWeek', '2026-03-08']);
  assert.equal(app.showPanel, false);
  assert.deepStrictEqual(writes, []);
  assert.equal(event.prevented, false);
});

test('a restore onto an event entry reopens it without moving history or date', async () => {
  const { app, ctx, writes, fc, location } = makeApp();
  ctx.fetch = async () => ({ ok: true, json: async () => rawEvent(EVENT_A) });
  location.search = `?view=month&date=2026-03-01&event=${EVENT_A}`;

  app.restoreFromUrl(restoreEvent());
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(app.showPanel, true);
  assert.equal(app.form.uuid, EVENT_A);
  assert.deepStrictEqual(fc.moves, ['2026-03-01']);
  assert.deepStrictEqual(writes, []);
});

test('a restore onto an entry from an earlier page load is rebuilt, not reloaded', () => {
  const { app } = makeApp();

  const event = restoreEvent(true);
  app.restoreFromUrl(event);

  assert.equal(event.prevented, true);
});

test('an event closed while it loads does not reopen the panel', async () => {
  const { app, ctx, writes } = makeApp();
  let respond;
  ctx.fetch = () => new Promise((resolve) => { respond = resolve; });

  const opening = app.openEventById(EVENT_A);
  app.closePanel();
  respond({ ok: true, json: async () => rawEvent(EVENT_A) });
  await opening;

  assert.equal(app.showPanel, false);
  assert.ok(!writes.some(([kind]) => kind === 'push'));
});


test('an action of the hover card is about the occurrence the card showed', async () => {
  const { app, ctx } = makeApp();
  ctx.document.querySelectorAll = () => [];
  const asked = [];
  const slot = '2026-03-27T09:00:00+00:00';
  ctx.fetch = async (url) => {
    asked.push(url);
    return { ok: true, json: async () => ({ ...rawEvent(EVENT_A), is_recurring: true, original_start: slot }) };
  };
  let opened = 0;
  app.openEditModal = () => { opened += 1; };

  await app.handleEventCardAction({ detail: { action: 'edit', eventId: EVENT_A, start: slot } });

  assert.equal(new URL(asked[0], 'http://x').searchParams.get('original_start'), slot);
  assert.equal(app._panelRaw.original_start, slot);
  assert.equal(opened, 1);

  // A card of an event that does not repeat names no slot.
  await app.handleEventCardAction({ detail: { action: 'edit', eventId: EVENT_B, start: '' } });
  assert.equal(asked[1], `/api/v1/events/${EVENT_B}`);
});
