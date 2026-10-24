'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const SCRIPT = 'workspace/calendar/ui/static/calendar/ui/js/calendar_calendars.js';

function makeApp(navigator) {
  const alerts = [];
  const ctx = loadScript(SCRIPT, { navigator });
  ctx.AppAlert = {
    success: (msg) => alerts.push(['success', msg]),
    error: (msg) => alerts.push(['error', msg]),
  };
  const app = {
    calendarFeed: { url: 'https://example.test/calendar/feeds/abc.ics' },
    ...ctx.calendarCalendarsMixin(),
  };
  return { app, alerts };
}

test('copying the feed link without a clipboard API reports the failure', () => {
  // navigator.clipboard is undefined outside a secure context (plain HTTP on a LAN address).
  const { app, alerts } = makeApp({});
  assert.doesNotThrow(() => app.copyCalendarFeed());
  assert.deepStrictEqual(alerts.map((a) => Array.from(a)), [['error', 'Failed to copy link']]);
});

test('copying the feed link writes it to the clipboard', async () => {
  const written = [];
  const { app, alerts } = makeApp({
    clipboard: { writeText: (text) => { written.push(text); return Promise.resolve(); } },
  });
  app.copyCalendarFeed();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepStrictEqual(written, ['https://example.test/calendar/feeds/abc.ics']);
  assert.deepStrictEqual(alerts.map((a) => Array.from(a)), [['success', 'Link copied!']]);
});
