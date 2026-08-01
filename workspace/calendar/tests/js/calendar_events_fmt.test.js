'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const mixinStub = () => ({});

function makeEventsMixin(userTz) {
  const ctx = loadScript('workspace/calendar/ui/static/calendar/ui/js/calendar_events.js', {
    document: { getElementById: () => null },
  });
  ctx.getUserTimeZone = () => userTz;
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
