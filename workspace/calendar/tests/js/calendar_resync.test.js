'use strict';

// resync() is what the calendar runs when the SSE stream comes back up - a
// resumed tab, or a bfcache restore after a mobile back gesture. Both the
// FullCalendar grid and the agenda list have to be refetched, or the user
// keeps looking at the events from before the freeze.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

function makeApp() {
  const ctx = loadScripts([
    'workspace/common/static/ui/js/zoned_formatter.js',
    'workspace/calendar/ui/static/calendar/ui/js/calendar.js',
  ], {
    document: { getElementById: () => null },
localStorage: { getItem: () => null, setItem: () => {} },
    sidebarPreference: { initial: () => false, save: () => {} },
    matchMedia: () => ({ matches: false, addEventListener: () => {}, addListener: () => {} }),
    calendarCalendarsMixin: () => ({}),
    calendarEventsMixin: () => ({}),
    calendarTasksMixin: () => ({}),
    calendarRecurrenceMixin: () => ({}),
    calendarPollsMixin: () => ({}),
  });

  const calls = { refetchEvents: 0, refetchAgenda: 0 };
  const app = ctx.calendarApp();
  app.calendar = { refetchEvents() { calls.refetchEvents++; } };
  app.refetchAgenda = () => { calls.refetchAgenda++; };
  return { app, calls };
}

test('resync refetches the grid and the agenda', () => {
  const { app, calls } = makeApp();

  app.resync();

  assert.equal(calls.refetchEvents, 1);
  assert.equal(calls.refetchAgenda, 1);
});

test('resync still refreshes the agenda before FullCalendar has mounted', () => {
  const { app, calls } = makeApp();
  // init() mounts FullCalendar on $nextTick, so a reconnect can land first.
  app.calendar = null;

  assert.doesNotThrow(() => app.resync());
  assert.equal(calls.refetchAgenda, 1);
});
