'use strict';

// The event form holds wall clocks in the user's configured zone. These tests
// pin the browser to Paris and the user elsewhere, on dates where only Paris
// switches to or from summer time, so a value read in the browser's zone
// lands on the wrong instant. Set before any Date is built.
process.env.TZ = 'Europe/Paris';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

const mixinStub = () => ({});

const localtimeCtx = loadScripts([
  'workspace/common/static/ui/js/zoned_formatter.js',
  'workspace/common/static/ui/js/localtime.js',
], {
  document: {
    documentElement: { getAttribute: () => null },
    body: {},
    querySelectorAll: () => [],
  },
  MutationObserver: class { observe() {} },
});

function makeApp(userTz, extraGlobals = {}) {
  const ctx = loadScripts([
    'workspace/common/static/ui/js/zoned_formatter.js',
    'workspace/calendar/ui/static/calendar/ui/js/calendar.js',
    'workspace/calendar/ui/static/calendar/ui/js/calendar_events.js',
  ], {
    document: { getElementById: () => null },
    localStorage: { getItem: () => null, setItem: () => {} },
    sidebarPreference: { initial: () => false, save: () => {} },
    matchMedia: () => ({ matches: false, addEventListener: () => {}, addListener: () => {} }),
    calendarCalendarsMixin: mixinStub,
    calendarTasksMixin: mixinStub,
    calendarRecurrenceMixin: mixinStub,
    calendarPollsMixin: mixinStub,
    ...extraGlobals,
  });
  ctx.getUserTimeZone = () => userTz;
  ctx.wallClockToIso = localtimeCtx.wallClockToIso;
  ctx.isoToWallClock = localtimeCtx.isoToWallClock;
  return Object.assign(ctx.calendarApp(), ctx.calendarEventsMixin());
}

test('the browser zone is pinned to Paris', () => {
  // 2026-03-29 02:30 does not exist in Paris: the clocks jump from 02:00 to 03:00.
  assert.equal(new Date('2026-03-29T02:30').getHours(), 3);
});

test('an end the browser zone cannot represent is not refused as before the start', async () => {
  const errors = [];
  const app = makeApp('UTC', {
    AppDialog: { error: async (opts) => errors.push(opts) },
    getCSRFToken: () => 'token',
    fetch: async () => ({ ok: false }),
  });
  Object.assign(app, {
    modalMode: 'create',
    selectedMembers: [],
    selectedGuests: [],
    buildRecurrenceRule: () => '',
    form: {
      title: 'Standup',
      start: '2026-03-29T02:30',
      end: '2026-03-29T03:00',
      all_day: false,
      calendar_id: 'c1',
      description: '',
      location: '',
    },
  });
  await app.saveEvent();
  assert.deepEqual(errors, []);
});

test('the duration shortcut measures the user-zone wall clock', () => {
  const app = makeApp('UTC');
  // Three hours in UTC; two in Paris, which loses an hour that night.
  app.form = { start: '2026-03-29T01:00', end: '2026-03-29T04:00', all_day: false };
  assert.equal(app.activeDuration(), 180);
});

test('the default end is one hour after the start across a DST switch', () => {
  const app = makeApp('UTC');
  // 00:30Z is 02:30 in Paris, summer time, half an hour before the clocks
  // fall back: the browser's 03:30 is then two hours later.
  assert.equal(app._addHour('2026-10-25T00:30:00Z'), '2026-10-25T01:30:00.000Z');
});
