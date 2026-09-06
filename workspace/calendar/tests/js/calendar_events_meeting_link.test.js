'use strict';

// createMeetingLink() must target the series master, not the occurrence the
// user happened to be looking at: a virtual occurrence's uuid is the
// synthetic "<master uuid>:<iso start>" built in recurrence.py (the create
// endpoint 400s on it), and a materialized exception's own uuid is a row
// that never legitimately owns a Meeting. Every sibling panel action reads
// `_panelRaw.master_event_id || form.uuid` for the same reason.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

// calendarEventsMixin() builds a day-key formatter from window.zonedFormatter
// as it is constructed, so that script loads first here exactly as base.html
// loads it before the mixin.
function makeApp(fetchImpl) {
  const ctx = loadScripts(
    [
      'workspace/common/static/ui/js/zoned_formatter.js',
      'workspace/calendar/ui/static/calendar/ui/js/calendar_events.js',
    ],
    {
      document: { getElementById: () => null },
      fetch: fetchImpl,
      getCSRFToken: () => 'csrf',
      AppAlert: { error() {}, success() {} },
      Intl,
    },
  );
  return ctx.calendarEventsMixin();
}

const jsonResponse = (data) => ({ ok: true, json: async () => data });

test('createMeetingLink posts the series master uuid for a recurring occurrence', async () => {
  const calls = [];
  const app = makeApp(async (url, opts) => {
    calls.push({ url, body: opts.body ? JSON.parse(opts.body) : null });
    return jsonResponse({ join_url: 'http://x/meet/s' });
  });
  app._panelRaw = { uuid: 'master-1:2026-09-05T10:00:00+00:00', master_event_id: 'master-1', join_url: null };
  app.form = { uuid: 'master-1:2026-09-05T10:00:00+00:00' };
  app.creatingMeeting = false;

  await app.createMeetingLink();

  // The POST, then the detail refetch that brings back the meeting links.
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, '/api/v1/chat/meetings');
  assert.equal(calls[0].body.event_id, 'master-1');
  assert.equal(app._panelRaw.join_url, 'http://x/meet/s');
});

test('createMeetingLink posts form.uuid, not _panelRaw.uuid, when it is not a recurring occurrence', async () => {
  const calls = [];
  const app = makeApp(async (url, opts) => {
    calls.push({ url, body: opts.body ? JSON.parse(opts.body) : null });
    return jsonResponse({ join_url: 'http://x/meet/s' });
  });
  // Deliberately different from form.uuid: every sibling action in this
  // file falls back to form.uuid, never _panelRaw.uuid directly, and this
  // is the one test that would catch swapping the two.
  app._panelRaw = { uuid: 'panel-raw-uuid', join_url: null };
  app.form = { uuid: 'form-uuid' };
  app.creatingMeeting = false;

  await app.createMeetingLink();

  assert.equal(calls[0].url, '/api/v1/chat/meetings');
  assert.equal(calls[0].body.event_id, 'form-uuid');
});

// The creation endpoint answers with the meeting alone, and the member room
// link is a property of the event (the server resolves who may open it), so
// the panel has to re-read the event or the owner sees no way in until a
// reload.
test('createMeetingLink refetches the event so the member room link appears', async () => {
  const calls = [];
  const app = makeApp(async (url, opts) => {
    calls.push(url);
    if (url === '/api/v1/chat/meetings') return jsonResponse({ join_url: 'http://x/meet/s' });
    return jsonResponse({
      uuid: 'master-1',
      join_url: 'http://x/meet/s',
      room_url: 'http://x/chat/room/c1',
    });
  });
  app._panelRaw = { uuid: 'master-1', join_url: null, room_url: null };
  app.form = { uuid: 'master-1' };
  app.creatingMeeting = false;

  await app.createMeetingLink();

  assert.deepEqual(calls, ['/api/v1/chat/meetings', '/api/v1/events/master-1']);
  assert.equal(app._panelRaw.join_url, 'http://x/meet/s');
  assert.equal(app._panelRaw.room_url, 'http://x/chat/room/c1');
});

test('createMeetingLink keeps the occurrence its panel shows when the detail answers the master', async () => {
  const app = makeApp(async (url) => {
    if (url === '/api/v1/chat/meetings') return jsonResponse({ join_url: 'http://x/meet/s' });
    return jsonResponse({
      uuid: 'master-1',
      start: '2026-09-05T10:00:00+00:00',
      end: '2026-09-05T11:00:00+00:00',
      join_url: 'http://x/meet/s',
      room_url: 'http://x/chat/room/c1',
    });
  });
  app._panelRaw = {
    uuid: 'master-1:2026-09-12T10:00:00+00:00',
    master_event_id: 'master-1',
    original_start: '2026-09-12T10:00:00+00:00',
    start: '2026-09-12T10:00:00+00:00',
    end: '2026-09-12T11:00:00+00:00',
    join_url: null,
    room_url: null,
  };
  app.form = { uuid: 'master-1:2026-09-12T10:00:00+00:00' };
  app.creatingMeeting = false;

  await app.createMeetingLink();

  // The panel's date line reads these instants, so the master's must not
  // overwrite them.
  assert.equal(app._panelRaw.start, '2026-09-12T10:00:00+00:00');
  assert.equal(app._panelRaw.end, '2026-09-12T11:00:00+00:00');
  assert.equal(app._panelRaw.uuid, 'master-1:2026-09-12T10:00:00+00:00');
  assert.equal(app._panelRaw.master_event_id, 'master-1');
  assert.equal(app._panelRaw.room_url, 'http://x/chat/room/c1');
});
