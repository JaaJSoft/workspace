'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

function load() {
  return loadScripts([
    'workspace/common/static/ui/js/zoned_formatter.js',
    'workspace/common/static/ui/js/localtime.js',
    'workspace/calendar/ui/static/calendar/ui/js/booking_public.js',
    'workspace/calendar/ui/static/calendar/ui/js/calendar_booking_pages.js',
  ], {
    document: {
      documentElement: { getAttribute: () => null, lang: 'en' },
      body: {},
      querySelectorAll: () => [],
    },
    MutationObserver: class { observe() {} },
    navigator: { languages: ['en-US'] },
  });
}

const ctx = load();
const utils = ctx.bookingUtils;

test('monthGrid lays a month out in Monday-first weeks', () => {
  // November 2026 starts on a Sunday and has 30 days.
  const weeks = Array.from(utils.monthGrid({ year: 2026, month: 11 }), (w) => Array.from(w));

  assert.equal(weeks.length, 6);
  assert.deepStrictEqual(weeks[0], [null, null, null, null, null, null, '2026-11-01']);
  assert.deepStrictEqual(weeks[1][0], '2026-11-02');
  assert.deepStrictEqual(weeks[5], ['2026-11-30', null, null, null, null, null, null]);
});

test('shiftMonth crosses year boundaries', () => {
  assert.deepStrictEqual({ ...utils.shiftMonth({ year: 2026, month: 12 }, 1) }, { year: 2027, month: 1 });
  assert.deepStrictEqual({ ...utils.shiftMonth({ year: 2027, month: 1 }, -1) }, { year: 2026, month: 12 });
});

test('monthRange bounds the month on the visitor wall clock, DST included', () => {
  // Paris is UTC+1 on 1 March and UTC+2 on 1 April.
  const range = Array.from(utils.monthRange({ year: 2027, month: 3 }, 'Europe/Paris'));

  assert.deepStrictEqual(range, ['2027-02-28T23:00:00.000Z', '2027-03-31T22:00:00.000Z']);
});

test('groupByDay files each slot under the visitor day, not the UTC one', () => {
  const slots = ['2026-11-09T23:30:00+00:00', '2026-11-10T08:00:00+00:00'];

  const tokyo = { ...utils.groupByDay(slots, 'Asia/Tokyo') };
  const newYork = { ...utils.groupByDay(slots, 'America/New_York') };

  assert.deepStrictEqual(Object.keys(tokyo), ['2026-11-10']);
  assert.deepStrictEqual(Object.keys(newYork).sort(), ['2026-11-09', '2026-11-10']);
});

test('monthOf reads the month in the zone', () => {
  // 31 Dec 23:30 UTC is already January in Tokyo.
  const date = new Date('2026-12-31T23:30:00Z');

  assert.deepStrictEqual({ ...utils.monthOf(date, 'Asia/Tokyo') }, { year: 2027, month: 1 });
  assert.deepStrictEqual({ ...utils.monthOf(date, 'UTC') }, { year: 2026, month: 12 });
});

test('zoneList keeps the current zone even when the browser does not list it', () => {
  const zones = Array.from(utils.zoneList('Etc/Unknown'));

  assert.equal(zones[0], 'Etc/Unknown');
});

test('weekly hours default to working days, 9 to 17', () => {
  const week = Array.from(ctx.bookingHours.defaultWeek(), (day) => Array.from(day, (r) => Array.from(r)));

  assert.equal(week.length, 7);
  assert.deepStrictEqual(week[0], [['09:00', '17:00']]);
  assert.deepStrictEqual(week[5], []);
  assert.deepStrictEqual(week[6], []);
});

test('a new range starts an hour after the last one of the day', () => {
  assert.deepStrictEqual(Array.from(ctx.bookingHours.nextRange([['09:00', '12:00']])), ['13:00', '14:00']);
  assert.deepStrictEqual(Array.from(ctx.bookingHours.nextRange([])), ['09:00', '17:00']);
  assert.deepStrictEqual(Array.from(ctx.bookingHours.nextRange([['18:00', '21:30']])), ['22:00', '23:00']);
  // Nothing fits after 22:00 without overlapping the last range.
  assert.equal(ctx.bookingHours.nextRange([['20:00', '23:30']]), null);
});

test('copyWeek does not share arrays with the page it copies', () => {
  const week = [[['09:00', '12:00']], [], [], [], [], [], []];
  const copy = ctx.bookingHours.copyWeek(week);
  copy[0][0][0] = '10:00';

  assert.equal(week[0][0][0], '09:00');
});
