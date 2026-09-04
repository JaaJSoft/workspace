'use strict';
const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function host(fetchImpl) {
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/meeting_host.js', {
    fetch: fetchImpl, getCSRFToken: () => 'csrf', AppAlert: { error() {}, success() {} },
  });
  const m = ctx.chatMeetingHostMixin();
  m.meeting = { uuid: 'm1', slug: 's', locked: false, join_url: 'http://x/meet/s' };
  m.callParticipants = [{ participant_key: 'u:1' }, { participant_key: 'g:9' }];
  m.callSession = { max_participants: 6 };
  m._csrf = () => 'csrf';
  return m;
}

test('the lobby loads from the host endpoint and a knock event refreshes it', async () => {
  const urls = [];
  const m = host(async (url) => { urls.push(url); return { ok: true, json: async () => [{ uuid: 'g9', display_name: 'Ana' }] }; });
  await m.loadLobby();
  assert.equal(urls[0], '/api/v1/chat/meetings/m1/lobby');
  assert.equal(m.lobby.length, 1);
  await m.onGuestWaiting({ meeting_id: 'm1', guest_uuid: 'g10', display_name: 'Bo' });
  assert.equal(urls.length, 2);
});

test('admit, refuse, remove, lock and end hit their endpoints with CSRF', async () => {
  const calls = [];
  const m = host(async (url, opts = {}) => { calls.push({ url, opts }); return { ok: true, json: async () => ({ locked: true }) }; });
  await m.admitGuest('g9'); await m.refuseGuest('g9'); await m.removeGuest('g:9'); await m.toggleLock(); await m.endMeeting();
  assert.deepStrictEqual(calls.map((c) => c.url), [
    '/api/v1/chat/meetings/m1/guests/g9/admit',
    '/api/v1/chat/meetings/m1/guests/g9/refuse',
    '/api/v1/chat/meetings/m1/guests/9/remove',
    '/api/v1/chat/meetings/m1/lock',
    '/api/v1/chat/meetings/m1/end',
  ]);
  for (const c of calls) assert.equal(c.opts.headers['X-CSRFToken'], 'csrf');
  assert.equal(m.meeting.locked, true);
});

test('capacity label and guest tile detection', () => {
  const m = host(async () => ({ ok: true, json: async () => ({}) }));
  assert.equal(m.capacityLabel(), '2 / 6');
  assert.equal(m.isGuestTile({ participant_key: 'g:9' }), true);
  assert.equal(m.isGuestTile({ participant_key: 'u:1' }), false);
});

function hostWithAlerts(fetchImpl) {
  const warnings = [];
  const errors = [];
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/meeting_host.js', {
    fetch: fetchImpl,
    getCSRFToken: () => 'csrf',
    AppAlert: {
      error(msg) { errors.push(msg); },
      success() {},
      warning(msg) { warnings.push(msg); },
    },
  });
  const m = ctx.chatMeetingHostMixin();
  m.meeting = { uuid: 'm1', slug: 's', locked: false, join_url: 'http://x/meet/s' };
  m._csrf = () => 'csrf';
  return { m, warnings, errors };
}

test('a 409 from end (nothing to end) warns and does not leave the room', async () => {
  const { m, warnings, errors } = hostWithAlerts(async () => ({
    ok: false, status: 409, json: async () => ({ detail: 'nothing to end' }),
  }));
  let left = false;
  m.leaveRoom = () => { left = true; };
  await m.endMeeting();
  assert.equal(left, false);
  assert.deepStrictEqual(warnings, ['There is no meeting in progress to end.']);
  assert.deepStrictEqual(errors, []);
});

test('a 200 from end leaves the room', async () => {
  const { m, warnings } = hostWithAlerts(async () => ({ ok: true, json: async () => ({ status: 'ok' }) }));
  let left = false;
  m.leaveRoom = () => { left = true; };
  await m.endMeeting();
  assert.equal(left, true);
  assert.deepStrictEqual(warnings, []);
});

// The knock fan-out rides the host's own mailbox and any of their tabs can
// drain it, so this room may simply never see the event. These pin the
// mitigation: a schedule, and a re-read whenever the global stream reconnects.
function hostWithTimers(meeting) {
  const state = { intervals: new Set(), listeners: [], loads: 0, next: 0 };
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/meeting_host.js', {
    fetch: async () => ({ ok: true, json: async () => [] }),
    getCSRFToken: () => 'csrf',
    AppAlert: { error() {}, success() {}, warning() {} },
    setInterval: (fn, ms) => { state.next += 1; state.intervals.add(state.next); state.fns = fn; state.ms = ms; return state.next; },
    clearInterval: (id) => { state.intervals.delete(id); },
    addEventListener: (name, fn) => { state.listeners.push(name); state.handler = fn; },
    removeEventListener: (name) => { state.listeners = state.listeners.filter((n) => n !== name); },
  });
  const m = ctx.chatMeetingHostMixin();
  m.meeting = meeting;
  m.loadLobby = async () => { state.loads += 1; };
  return { m, state };
}

test('a room with no meeting arms no lobby refresh', () => {
  const { m, state } = hostWithTimers(null);
  m._startLobbyRefresh();
  assert.equal(state.intervals.size, 0);
  assert.deepStrictEqual(state.listeners, []);
});

test('a meeting room re-reads the lobby on a schedule and on a stream reconnect', async () => {
  const { m, state } = hostWithTimers({ uuid: 'm1' });
  m._startLobbyRefresh();
  assert.equal(state.intervals.size, 1);
  assert.equal(state.ms, 30000);
  assert.deepStrictEqual(state.listeners, ['sse:reconnect']);

  await state.fns();
  await state.handler();
  assert.equal(state.loads, 2);
});

test('arming twice keeps one timer, and stopping releases both hooks', () => {
  const { m, state } = hostWithTimers({ uuid: 'm1' });
  m._startLobbyRefresh();
  m._startLobbyRefresh();
  assert.equal(state.intervals.size, 1);

  m._stopLobbyRefresh();
  assert.equal(state.intervals.size, 0);
  assert.deepStrictEqual(state.listeners, []);
});
