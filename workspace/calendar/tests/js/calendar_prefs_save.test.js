'use strict';

// The calendar's preferences are put as one object under
// calendar/preferences, and a failed write never escapes.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');
const { sidebarPreferenceStub } = require('../../../core/tests/js/sidebar_preference_stub');

function makeApp(reply = { ok: true, status: 200 }) {
  const puts = [];
  const ctx = loadScripts([
    'workspace/core/static/core/js/user_settings.js',
    'workspace/common/static/ui/js/zoned_formatter.js',
    'workspace/common/static/ui/js/uuid.js',
    'workspace/calendar/ui/static/calendar/ui/js/calendar_events.js',
    'workspace/calendar/ui/static/calendar/ui/js/calendar.js',
  ], {
    getCSRFToken: () => 'token',
    fetch: (url, options) => {
      puts.push({ url, method: options.method, body: JSON.parse(options.body) });
      return reply instanceof Error ? Promise.reject(reply) : Promise.resolve(reply);
    },
    document: { getElementById: () => null, body: { dataset: { userId: '1' } } },
    localStorage: { getItem: () => null, setItem: () => {} },
    sidebarPreference: sidebarPreferenceStub(),
    matchMedia: () => ({ matches: false, addEventListener: () => {} }),
    calendarCalendarsMixin: () => ({}),
    calendarTasksMixin: () => ({}),
    calendarRecurrenceMixin: () => ({}),
    calendarPollsMixin: () => ({}),
    location: { pathname: '/calendar', search: '' },
    navHistory: { push() {}, replace() {}, returnTo() { return false; } },
    URLSearchParams,
  });
  const app = ctx.calendarApp();
  app.calendar = null;
  return { app, puts };
}

test('a preference puts the whole object under calendar/preferences', () => {
  const { app, puts } = makeApp();
  app.updatePref('weekNumbers', true);

  assert.equal(puts.length, 1);
  assert.equal(puts[0].url, '/api/v1/settings/calendar/preferences');
  assert.equal(puts[0].method, 'PUT');
  assert.equal(puts[0].body.value.weekNumbers, true);
  assert.equal(app.prefs.weekNumbers, true);
});

test('a refused or lost write is swallowed, the preference stays applied', async () => {
  for (const reply of [{ ok: false, status: 400 }, new TypeError('offline')]) {
    const { app } = makeApp(reply);
    app.updatePref('firstDay', 1);
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(app.prefs.firstDay, 1);
  }
});
