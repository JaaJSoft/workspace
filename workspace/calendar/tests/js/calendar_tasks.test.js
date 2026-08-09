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
  project_uuid: '019fe2ba-0000-7280-b868-646de6d74f6a',
  project_name: 'Website',
  url: '/projects/019fe2ba-0000-7280-b868-646de6d74f6a/board?task=019fe2ba-ee80-7280-b868-646de6d74f6a',
};

/** Build the mixin over a minimal host component, with fetch stubbed. */
function makeApp({ showTasks = true, response, reject = false } = {}) {
  const calls = [];
  const ctx = loadScript(SCRIPT, {
    fetch: (url) => {
      calls.push(url);
      if (reject) return Promise.reject(new Error('offline'));
      return Promise.resolve(response);
    },
  });
  const app = { prefs: { showTasks }, ...ctx.calendarTasksMixin() };
  return { app, calls };
}

const okResponse = (data) => ({ ok: true, json: () => Promise.resolve(data) });

test('maps tasks to all-day events keyed apart from event uuids', async () => {
  const { app, calls } = makeApp({ response: okResponse([TASK]) });

  const events = await app.fetchTaskEvents('2026-08-01', '2026-09-01');

  assert.equal(calls.length, 1);
  assert.equal(
    calls[0],
    '/api/v1/projects/tasks/calendar?start=2026-08-01&end=2026-09-01',
  );
  assert.equal(events.length, 1);
  const event = { ...events[0] };
  assert.equal(event.id, `task-${TASK.uuid}`);
  assert.equal(event.title, 'Ship it');
  assert.equal(event.start, '2026-08-15');
  assert.equal(event.allDay, true);
  assert.deepEqual(Array.from(event.classNames), ['event-task', 'event-task-urgent']);
  assert.equal(event.extendedProps._task.reference, 'WR-42');
});

test('encodes range boundaries carrying a timezone offset', async () => {
  const { app, calls } = makeApp({ response: okResponse([]) });

  await app.fetchTaskEvents('2026-08-01T00:00:00+02:00', '2026-09-01T00:00:00+02:00');

  assert.equal(
    calls[0],
    '/api/v1/projects/tasks/calendar' +
      '?start=2026-08-01T00%3A00%3A00%2B02%3A00&end=2026-09-01T00%3A00%3A00%2B02%3A00',
  );
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
