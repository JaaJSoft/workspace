'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const SCRIPT = 'workspace/calendar/ui/static/calendar/ui/js/calendar_tasks.js';

const TASK = {
  uuid: '019fe2ba-ee80-7280-b868-646de6d74f6a',
  title: 'Ship it',
  due_date: '2026-08-15',
  priority: 'urgent',
  reference: 'WR-42',
  project: {
    uuid: '019fe2ba-0000-7280-b868-646de6d74f6a',
    name: 'Website',
    key: 'WR',
  },
  url: '/projects/019fe2ba-0000-7280-b868-646de6d74f6a/board?task=019fe2ba-ee80-7280-b868-646de6d74f6a',
};

/** Build the mixin over a minimal host component, with fetch stubbed.
 *  `response` answers every request, `pages` one request each, in order. */
function makeApp({ showTasks = true, response, pages, reject = false } = {}) {
  const calls = [];
  const ctx = loadScript(SCRIPT, {
    URLSearchParams,
    fetch: (url) => {
      calls.push(url);
      if (reject) return Promise.reject(new Error('offline'));
      return Promise.resolve(pages ? pages.shift() : response);
    },
  });
  const app = { prefs: { showTasks }, ...ctx.calendarTasksMixin() };
  return { app, calls };
}

const okResponse = (data, hasMore = false) => ({
  ok: true,
  headers: { get: (name) => (name === 'X-Has-More' ? String(hasMore) : null) },
  json: () => Promise.resolve(data),
});

const LIST = '/api/v1/projects/tasks';
const WINDOW = 'due_after=2026-08-01&due_before=2026-08-31&completed=false&ordering=due_date&limit=1000';

test('maps tasks to all-day events keyed apart from event uuids', async () => {
  const { app, calls } = makeApp({ response: okResponse([TASK]) });

  const events = await app.fetchTaskEvents('2026-08-01', '2026-09-01');

  assert.equal(calls.length, 1);
  // The grid's last day is left out by FullCalendar and taken in by the list.
  assert.equal(calls[0], `${LIST}?${WINDOW}&offset=0`);
  assert.equal(events.length, 1);
  const event = { ...events[0] };
  assert.equal(event.id, `task-${TASK.uuid}`);
  assert.equal(event.title, 'Ship it');
  assert.equal(event.start, '2026-08-15');
  assert.equal(event.allDay, true);
  assert.deepEqual(Array.from(event.classNames), ['event-task', 'event-task-urgent']);
  assert.equal(event.extendedProps._task.reference, 'WR-42');
});

test('reads the grid days off boundaries carrying a timezone offset', async () => {
  const { app, calls } = makeApp({ response: okResponse([]) });

  // Normalizing to UTC first would shift the window by one day east of it.
  await app.fetchTaskEvents('2026-08-01T00:00:00+02:00', '2026-09-01T00:00:00+02:00');

  assert.equal(calls[0], `${LIST}?${WINDOW}&offset=0`);
});

test('the day before the first of a month or a year is the last of the previous one', async () => {
  const { app, calls } = makeApp({ response: okResponse([]) });

  await app.fetchTaskEvents('2025-12-29', '2026-01-01');
  await app.fetchTaskEvents('2028-02-01', '2028-03-01');

  assert.match(calls[0], /due_after=2025-12-29&due_before=2025-12-31&/);
  assert.match(calls[1], /due_after=2028-02-01&due_before=2028-02-29&/);
});

test('asks for the next page while the list says there is one', async () => {
  const second = { ...TASK, uuid: '019fe2ba-ee80-7280-b868-000000000002' };
  const { app, calls } = makeApp({
    pages: [okResponse([TASK], true), okResponse([second], false)],
  });

  const events = await app.fetchTaskEvents('2026-08-01', '2026-09-01');

  assert.deepEqual(Array.from(calls), [
    `${LIST}?${WINDOW}&offset=0`,
    `${LIST}?${WINDOW}&offset=1000`,
  ]);
  assert.equal(events.length, 2);
});

test('skips the request entirely when the overlay is toggled off', async () => {
  const { app, calls } = makeApp({ showTasks: false, response: okResponse([TASK]) });

  assert.deepEqual(await app.fetchTaskEvents('2026-08-01', '2026-09-01'), []);
  assert.equal(calls.length, 0);
});

test('an overlay failure yields no events instead of rejecting', async () => {
  // FullCalendar drops every source's result when one rejects, so a failing
  // task fetch must not take the events source down with it.
  const failures = [
    makeApp({ reject: true }),
    makeApp({ response: { ok: false, json: () => Promise.resolve([]) } }),
  ];
  for (const { app } of failures) {
    assert.deepEqual(await app.fetchTaskEvents('2026-08-01', '2026-09-01'), []);
  }
});
