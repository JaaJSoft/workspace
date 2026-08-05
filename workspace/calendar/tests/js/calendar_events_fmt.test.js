'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const mixinStub = () => ({});

// In the browser localtime.js shares the window with every calendar
// script; load the real production helpers once and mirror them into
// each vm realm so the tests exercise the shared implementation.
const localtimeCtx = loadScript('workspace/common/static/ui/js/localtime.js', {
  document: {
    documentElement: { getAttribute: () => null },
    body: {},
    querySelectorAll: () => [],
  },
  MutationObserver: class { observe() {} },
});

function injectTzHelpers(ctx) {
  ctx.wallClockToIso = localtimeCtx.wallClockToIso;
  ctx.isoToWallClock = localtimeCtx.isoToWallClock;
}

function makeEventsMixin(userTz) {
  const ctx = loadScript('workspace/calendar/ui/static/calendar/ui/js/calendar_events.js', {
    document: { getElementById: () => null },
  });
  ctx.getUserTimeZone = () => userTz;
  injectTzHelpers(ctx);
  return { ctx, mixin: ctx.calendarEventsMixin() };
}

function makeCalendarApp(userTz) {
  const ctx = loadScript('workspace/calendar/ui/static/calendar/ui/js/calendar.js', {
    document: { getElementById: () => null },
    localStorage: { getItem: () => null, setItem: () => {} },
    matchMedia: () => ({ matches: false, addEventListener: () => {}, addListener: () => {} }),
    calendarCalendarsMixin: mixinStub,
    calendarEventsMixin: mixinStub,
    calendarRecurrenceMixin: mixinStub,
    calendarPollsMixin: mixinStub,
  });
  ctx.getUserTimeZone = () => userTz;
  injectTzHelpers(ctx);
  return ctx.calendarApp();
}

test('agendaByDay groups all-day events on their UTC calendar day', () => {
  const { mixin } = makeEventsMixin('America/Los_Angeles');
  mixin.agenda = {
    events: [
      { uuid: 'a', title: 'All day', start: '2026-08-01T00:00:00Z', all_day: true },
    ],
  };
  const groups = mixin.agendaByDay();
  assert.equal(groups.length, 1);
  assert.equal(groups[0].date, '2026-08-01');
  assert.match(groups[0].label, /August 1|1 août/);
  assert.doesNotMatch(groups[0].label, /July 31|31 juillet/);
});

test('agendaByDay groups timed events on the user-timezone day', () => {
  const { mixin } = makeEventsMixin('Asia/Tokyo');
  mixin.agenda = {
    events: [
      { uuid: 'b', title: 'Late call', start: '2026-01-31T20:00:00Z', all_day: false },
    ],
  };
  const groups = mixin.agendaByDay();
  // 20:00 UTC on Jan 31 is already Feb 1 in Tokyo.
  assert.equal(groups[0].date, '2026-02-01');
  assert.match(groups[0].label, /February 1|1 février/);
});

test('panelDateDisplay keeps a single-day all-day event on its day', () => {
  const { mixin } = makeEventsMixin('America/Los_Angeles');
  const app = makeCalendarApp('America/Los_Angeles');
  // panelDateDisplay lives in the events mixin but formats through the
  // root app helpers, exactly like the spread composition at runtime.
  const merged = Object.assign({}, app, mixin, {
    _panelRaw: { start: '2026-08-01', end: '2026-08-02', all_day: true },
    prefs: { timeFormat: '24h' },
  });
  const label = merged.panelDateDisplay();
  assert.match(label, /August 1|1 août/);
  assert.doesNotMatch(label, /July 31|31 juillet|August 2|2 août/);
});

test('panel labels read raw instants, not the wall-clock form values', () => {
  const { mixin } = makeEventsMixin('Asia/Vladivostok');
  const app = makeCalendarApp('Asia/Vladivostok');
  const merged = Object.assign({}, app, mixin, {
    // Raw occurrence: 11:42Z = 21:42 in Vladivostok (+10).
    _panelRaw: {
      start: '2026-08-13T11:42:59+00:00',
      end: '2026-08-13T13:12:59+00:00',
      all_day: false,
    },
    // The form holds user-zone wall-clock strings for the inputs; feeding
    // them back through the instant formatters double-converts (the
    // regression showed 05:42 for a 21:42 event).
    form: { start: '2026-08-13T21:42', end: '2026-08-13T23:12', all_day: false },
    prefs: { timeFormat: '24h' },
  });
  assert.match(merged.panelTimeLabel(), /21:42/);
  assert.doesNotMatch(merged.panelTimeLabel(), /05:42|07:42/);
  // Same-day events keep the times on the clock line only.
  assert.doesNotMatch(merged.panelDateDisplay(), /21:42|05:42/);
  assert.match(merged.panelDateDisplay(), /August 13|13 ao\xc3\xbbt|13 ao\u00fbt/);
});

test('openCreateModal keeps date-only clicks on their day with verbatim default times', () => {
  const { mixin } = makeEventsMixin('Asia/Vladivostok');
  const app = makeCalendarApp('Asia/Vladivostok');
  const merged = Object.assign({}, app, mixin, {
    showModal: false,
    showPanel: false,
    modalMode: '',
    ownedCalendars: [],
    selectedMembers: [],
    prefs: { defaultAllDay: false, timeFormat: '24h' },
  });
  // A month-view click hands over a date-only string: the prefill must be
  // that day at 09:00 verbatim, not a converted (shifted) wall clock.
  merged.openCreateModal('2026-08-05', '', false);
  assert.equal(merged.form.start, '2026-08-05T09:00');
  assert.equal(merged.form.end, '2026-08-05T10:00');
});

test('openCreateModal converts real instants to the configured wall clock', () => {
  const { mixin, ctx } = makeEventsMixin('Asia/Vladivostok');
  const app = makeCalendarApp('Asia/Vladivostok');
  const merged = Object.assign({}, app, mixin, {
    showModal: false,
    showPanel: false,
    modalMode: '',
    ownedCalendars: [],
    selectedMembers: [],
    prefs: { defaultAllDay: false, timeFormat: '24h' },
  });
  // 11:42Z is 21:42 in Vladivostok (+10).
  merged.openCreateModal('2026-08-05T11:42:00Z', '', false);
  assert.equal(merged.form.start, '2026-08-05T21:42');
});

test('applyDuration adds to the user-zone wall clock, not the browser parse', () => {
  const { mixin } = makeEventsMixin('Asia/Vladivostok');
  const app = makeCalendarApp('Asia/Vladivostok');
  const merged = Object.assign({}, app, mixin, {
    form: { start: '2026-08-05T09:00', end: '', all_day: false },
    prefs: { timeFormat: '24h' },
  });
  merged.applyDuration(60);
  assert.equal(merged.form.end, '2026-08-05T10:00');
});

test('applyDuration on all-day events does pure day-label arithmetic', () => {
  const { mixin } = makeEventsMixin('America/Los_Angeles');
  const app = makeCalendarApp('America/Los_Angeles');
  const merged = Object.assign({}, app, mixin, {
    form: { start: '2026-08-05', end: '', all_day: true },
    prefs: { timeFormat: '24h' },
  });
  // The end input names the last covered day: 1 day stays on the start day,
  // 3 days runs through the 7th.
  merged.applyDuration(1440);
  assert.equal(merged.form.end, '2026-08-05');
  assert.equal(merged.activeDuration(), 1440);
  merged.applyDuration(3 * 1440);
  assert.equal(merged.form.end, '2026-08-07');
  assert.equal(merged.activeDuration(), 3 * 1440);
});

test('openCreateModal keeps the last covered day of a multi-day date-only drag', () => {
  const { mixin } = makeEventsMixin('Asia/Vladivostok');
  const app = makeCalendarApp('Asia/Vladivostok');
  const merged = Object.assign({}, app, mixin, {
    showModal: false,
    showPanel: false,
    modalMode: '',
    ownedCalendars: [],
    selectedMembers: [],
    prefs: { defaultAllDay: false, timeFormat: '24h' },
  });
  // FullCalendar select() hands over an exclusive date-only end.
  merged.openCreateModal('2026-08-05', '2026-08-08', false);
  assert.equal(merged.form.start, '2026-08-05T09:00');
  assert.equal(merged.form.end, '2026-08-07T10:00');
});

function makeModalHarness(tz, extra) {
  const { ctx, mixin } = makeEventsMixin(tz);
  // openViewPanel reads the signed-in user off the body dataset.
  ctx.document.body = { dataset: { userId: '1' } };
  const app = makeCalendarApp(tz);
  return Object.assign({}, app, mixin, {
    showModal: false,
    showPanel: false,
    modalMode: '',
    ownedCalendars: [],
    selectedMembers: [],
    eventMembers: [],
    loadingEvent: false,
    prefs: { defaultAllDay: true, timeFormat: '24h' },
  }, extra || {});
}

test('an all-day drag prefills the last covered day, not the exclusive end', () => {
  const merged = makeModalHarness('Asia/Vladivostok');
  // FullCalendar select() over Aug 5-7 hands over Aug 8 as the exclusive end.
  merged.openCreateModal('2026-08-05', '2026-08-08', true);
  assert.equal(merged.form.start, '2026-08-05');
  assert.equal(merged.form.end, '2026-08-07');
});

test('editing an all-day event shows the last covered day in the end input', () => {
  const merged = makeModalHarness('America/Los_Angeles');
  // Stored exclusive end: Aug 5 -> Aug 7 covers three days.
  merged.openViewPanel({
    uuid: 'e1',
    calendar_id: 'c1',
    title: 'Trip',
    start: '2026-08-05',
    end: '2026-08-08',
    all_day: true,
    owner: { id: 1 },
    members: [],
  });
  assert.equal(merged.form.start, '2026-08-05');
  assert.equal(merged.form.end, '2026-08-07');
});

test('a degenerate all-day end never renders a backwards range', () => {
  const merged = makeModalHarness('America/Los_Angeles');
  // An import with DTEND == DTSTART would otherwise show Aug 4 -> Aug 5.
  merged.openViewPanel({
    uuid: 'e2',
    calendar_id: 'c1',
    title: 'Legacy',
    start: '2026-08-05',
    end: '2026-08-05',
    all_day: true,
    owner: { id: 1 },
    members: [],
  });
  assert.equal(merged.form.end, '2026-08-05');
});

test('all-day form boundaries round-trip the exclusive API end', () => {
  const merged = makeModalHarness('Asia/Vladivostok');
  // A two-day event (Aug 5 through Aug 6) must reach the API as Aug 7 so the
  // grid paints both days instead of one.
  assert.equal(merged._allDayPayloadEnd('2026-08-06'), '2026-08-07');
  assert.equal(merged._allDayFormEnd('2026-08-05', '2026-08-07'), '2026-08-06');
  // Single-day: start and end labels match, the API gets the next day.
  assert.equal(merged._allDayPayloadEnd('2026-08-05'), '2026-08-06');
  // Month rollover stays on pure day labels.
  assert.equal(merged._allDayPayloadEnd('2026-08-31'), '2026-09-01');
  assert.equal(merged._allDayFormEnd('2026-08-30', '2026-09-01'), '2026-08-31');
});
