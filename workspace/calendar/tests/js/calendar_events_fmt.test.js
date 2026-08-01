'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const mixinStub = () => ({});

// In the browser localtime.js shares the window with every calendar
// script; mirror its wall-clock helpers into a vm realm.
function injectTzHelpers(ctx) {
  const parts = (d, tz) => {
    const out = {};
    const dtf = new Intl.DateTimeFormat('en-CA', {
      timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
    });
    for (const part of dtf.formatToParts(d)) out[part.type] = part.value;
    return out;
  };
  const offsetMs = (tz, date) => {
    const p = parts(date, tz);
    return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) - date.getTime();
  };
  ctx.wallClockToIso = (naive, tz) => {
    if (!naive) return null;
    if (!tz) return new Date(naive.length === 10 ? naive + 'T00:00' : naive).toISOString();
    const [datePart, timePart] = naive.split('T');
    const [y, mo, d] = datePart.split('-').map(Number);
    const [h = 0, mi = 0, sec = 0] = (timePart || '00:00').split(':').map(Number);
    const guess = Date.UTC(y, mo - 1, d, h, mi, sec);
    let ts = guess - offsetMs(tz, new Date(guess));
    ts = guess - offsetMs(tz, new Date(ts));
    return new Date(ts).toISOString();
  };
  ctx.isoToWallClock = (iso, tz) => {
    const p = parts(new Date(iso), tz);
    return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
  };
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
  merged.applyDuration(1440);
  assert.equal(merged.form.end, '2026-08-06');
});
