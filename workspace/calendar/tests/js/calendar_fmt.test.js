'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

function makeApp(userTz) {
  const ctx = loadScripts([
    'workspace/common/static/ui/js/zoned_formatter.js',
    'workspace/calendar/ui/static/calendar/ui/js/calendar.js',
  ], {
    document: { getElementById: () => null },
localStorage: { getItem: () => null, setItem: () => {} },
    sidebarPreference: { initial: () => false, save: () => {} },
    matchMedia: () => ({ matches: false, addEventListener: () => {}, addListener: () => {} }),
    // The domain mixins live in separate files; the formatting helpers under
    // test are defined in calendar.js itself, so empty stubs are enough.
    calendarCalendarsMixin: () => ({}),
    calendarEventsMixin: () => ({}),
    calendarTasksMixin: () => ({}),
    calendarRecurrenceMixin: () => ({}),
    calendarPollsMixin: () => ({}),
  });
  ctx.getUserTimeZone = () => userTz;
  return { ctx, app: ctx.calendarApp() };
}

test('_fmtDate keeps all-day (date-only) values on their calendar day in western zones', () => {
  const { app } = makeApp('America/Los_Angeles');
  // Date-only strings parse as UTC midnight; a naive conversion shows July 31.
  assert.match(app._fmtDate('2026-08-01'), /August 1/);
  assert.doesNotMatch(app._fmtDate('2026-08-01'), /July 31/);
});

test('_fmtDate formats timed events in the configured timezone', () => {
  const { app } = makeApp('Asia/Tokyo');
  // 20:00 UTC on Jan 31 is already Feb 1 in Tokyo.
  assert.match(app._fmtDate('2026-01-31T20:00:00Z'), /February 1/);
});

test('_fmtDate year suffix follows the configured timezone', () => {
  const { app } = makeApp('Asia/Tokyo');
  // 23:30 UTC on Dec 31, 2098 is already 2099 in Tokyo.
  const label = app._fmtDate('2098-12-31T23:30:00Z');
  assert.match(label, /2099/);
  assert.doesNotMatch(label, /2098/);
});

test('_sameDay compares date-only values without timezone shift', () => {
  const { app } = makeApp('America/Los_Angeles');
  assert.equal(app._sameDay('2026-08-01', '2026-08-01'), true);
  assert.equal(app._sameDay('2026-08-01', '2026-08-02'), false);
});

// Counts Intl.DateTimeFormat constructions. Shadows the context's own
// Intl: the script looks the global up on every call, so the count sees
// each constructor invocation.
function countFormatters(ctx) {
  const counter = { built: 0 };
  ctx.Intl = {
    DateTimeFormat: function (...args) {
      counter.built++;
      return new Intl.DateTimeFormat(...args);
    },
  };
  return counter;
}

test('_fmtDate builds one day-key formatter per zone, not one per value', () => {
  const { ctx, app } = makeApp('Asia/Tokyo');
  const counter = countFormatters(ctx);
  for (let i = 1; i <= 28; i++) app._fmtDate(`2026-01-${String(i).padStart(2, '0')}T20:00:00Z`);
  assert.equal(counter.built, 1);
  app._fmtDate('2026-01-31T20:00:00Z');
  assert.equal(counter.built, 1);
  // All-day values are keyed in UTC: another zone, another cached formatter.
  app._fmtDate('2026-08-01');
  app._fmtDate('2026-08-02');
  assert.equal(counter.built, 2);
});
