'use strict';

// createMeetingLink() must target the series master, not the occurrence the
// user happened to be looking at: a virtual occurrence's uuid is the
// synthetic "<master uuid>:<iso start>" built in recurrence.py (the create
// endpoint 400s on it), and a materialized exception's own uuid is a row
// that never legitimately owns a Meeting. Every sibling panel action reads
// `_panelRaw.master_event_id || form.uuid` for the same reason.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function makeApp(fetchImpl) {
  const ctx = loadScript('workspace/calendar/ui/static/calendar/ui/js/calendar_events.js', {
    document: { getElementById: () => null },
    fetch: fetchImpl,
    getCSRFToken: () => 'csrf',
    AppAlert: { error() {}, success() {} },
  });
  return ctx.calendarEventsMixin();
}

const jsonResponse = (data) => ({ ok: true, json: async () => data });

test('createMeetingLink posts the series master uuid for a recurring occurrence', async () => {
  const calls = [];
  const app = makeApp(async (url, opts) => {
    calls.push({ url, body: JSON.parse(opts.body) });
    return jsonResponse({ join_url: 'http://x/meet/s' });
  });
  app._panelRaw = { uuid: 'master-1:2026-09-05T10:00:00+00:00', master_event_id: 'master-1', join_url: null };
  app.form = { uuid: 'master-1:2026-09-05T10:00:00+00:00' };
  app.creatingMeeting = false;

  await app.createMeetingLink();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/v1/chat/meetings');
  assert.equal(calls[0].body.event_id, 'master-1');
  assert.equal(app._panelRaw.join_url, 'http://x/meet/s');
});

test('createMeetingLink posts the panel uuid when it is not a recurring occurrence', async () => {
  const calls = [];
  const app = makeApp(async (url, opts) => {
    calls.push({ url, body: JSON.parse(opts.body) });
    return jsonResponse({ join_url: 'http://x/meet/s' });
  });
  app._panelRaw = { uuid: 'evt-1', join_url: null };
  app.form = { uuid: 'evt-1' };
  app.creatingMeeting = false;

  await app.createMeetingLink();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].body.event_id, 'evt-1');
});
