'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

const MEETING = {
  uuid: 'm-1',
  slug: 'abc123',
  title: 'Parity Check',
  join_url: 'https://example.test/meetings/abc123',
  locked: false,
  next_start: '2026-09-07T09:55:00Z',
  public_link_enabled: true,
  ad_hoc: false,
};

// The host page runs inside the app shell, but the vm has no DOM: it gets the
// handful of globals the real mixins reach for at construction and in init().
function app(fetchImpl, extra = {}) {
  const timers = { intervals: new Set(), listeners: [] };
  let nextTimer = 0;
  const ctx = loadScripts(
    [
      'workspace/common/static/ui/js/attachment_input.js',
      'workspace/chat/ui/static/chat/ui/js/ui_helpers.js',
      'workspace/chat/ui/static/chat/ui/js/input.js',
      'workspace/chat/ui/static/chat/ui/js/call.js',
      'workspace/chat/ui/static/chat/ui/js/call_room.js',
      'workspace/chat/ui/static/chat/ui/js/meeting_host.js',
      'workspace/chat/ui/static/chat/ui/js/meeting_chat.js',
      'workspace/chat/ui/static/chat/ui/js/meeting_page.js',
    ],
    {
      getCSRFToken: () => 'csrf-token',
      AppAlert: { error() {}, warning() {}, success() {} },
      chatCallShouldOwnMedia: () => true,
      fetch: fetchImpl,
      setInterval: () => { nextTimer += 1; timers.intervals.add(nextTimer); return nextTimer; },
      clearInterval: (id) => { timers.intervals.delete(id); },
      setTimeout: () => 0,
      clearTimeout: () => {},
      matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
      document: {
        getElementById: (id) => (id === 'meeting-data'
          ? { textContent: JSON.stringify(MEETING) }
          : null),
        addEventListener() {},
        createElement: () => ({ setAttribute() {}, appendChild() {}, classList: { add() {} } }),
      },
      navigator: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [] }) } },
      ...extra,
    },
  );
  ctx.addEventListener = (name, fn) => { timers.listeners.push([name, fn]); };
  ctx.removeEventListener = (name, fn) => {
    const at = timers.listeners.findIndex(([n, f]) => n === name && f === fn);
    if (at !== -1) timers.listeners.splice(at, 1);
  };
  const instance = ctx.chatMeetingHostApp(7);
  instance.$refs = {};
  instance.$nextTick = (fn) => { if (fn) fn(); };
  return { a: instance, ctx, timers };
}

const okFetch = async () => ({ ok: true, status: 200, json: async () => ({}) });

test('the host app derives its participant key and talks to the meeting call endpoints', async () => {
  const { a } = app(okFetch);
  a.meeting = MEETING;

  assert.equal(a.currentParticipantKey, 'u:7');
  assert.equal(a._callEndpoint('join'), '/api/v1/chat/meetings/m-1/call/join');
  assert.equal(a._callEndpoint(''), '/api/v1/chat/meetings/m-1/call');
  assert.equal(a._leaveTarget(), 'm-1');
});

test('the heartbeat is addressed through the leave target, not a conversation id', async () => {
  const calls = [];
  const { a } = app(async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    return { ok: true, status: 200, json: async () => ({}) };
  });
  a.meeting = MEETING;
  a.inCall = true;
  // A meeting call state carries no conversation_id at all, which is exactly
  // what the old conversation-keyed guard would have stopped on.
  a.callSession = { session_id: 's1', conversation_id: null };

  await a._sendHeartbeat();

  assert.deepStrictEqual(
    calls.map((c) => c.url),
    ['/api/v1/chat/meetings/m-1/call/heartbeat'],
  );
});

test('the meeting chat seam pages with a cursor and sends the CSRF token', () => {
  const { a } = app(okFetch);
  a.meeting = MEETING;

  assert.equal(a._meetingMessagesUrl(null), '/api/v1/chat/meetings/m-1/messages');
  assert.ok(a._meetingMessagesUrl('abc').endsWith('?before=abc'));
  assert.equal(a._meetingMessageHeaders()['X-CSRFToken'], 'csrf-token');
  assert.equal(a._canDeleteMeetingMessages(), true);
});

test('call_started for another meeting is ignored', async () => {
  let states = 0;
  const { a } = app(async () => {
    states += 1;
    return { ok: true, status: 200, json: async () => ({ active: false }) };
  });
  a.meeting = MEETING;

  await a.onCallStarted({ meeting_id: 'someone-elses-meeting' });
  assert.equal(states, 0);

  await a.onCallStarted({ meeting_id: 'm-1' });
  assert.equal(states, 1);
});

test('destroy releases the lobby refresh timer and its reconnect listener', () => {
  const { a, timers } = app(okFetch);
  a.meeting = MEETING;

  a._startLobbyRefresh();
  assert.equal(timers.intervals.size, 1);
  assert.equal(timers.listeners.length, 1);

  a.destroy();

  assert.equal(timers.intervals.size, 0, 'the interval is cleared');
  assert.equal(timers.listeners.length, 0, 'and the sse:reconnect listener released');
});

test('the idle card reads the meeting title and its next start off the payload', () => {
  const { a } = app(okFetch);
  a.meeting = MEETING;

  assert.equal(a.meetingTitle(), 'Parity Check');
  assert.ok(a.summaryLine());

  a.meeting = { ...MEETING, title: '', next_start: null };
  assert.equal(a.meetingTitle(), 'Meeting');
  assert.equal(a.summaryLine(), '');
});

test('a line arriving with the slide-over closed is counted, and reading clears it', () => {
  // The stage dock draws the badge below md, so the host app has to carry the
  // count the shared partial binds - an undefined one is a console error and a
  // badge that never appears.
  const { a } = app(okFetch, {
    matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  });
  a.meeting = MEETING;

  a.onMeetingMessage({ meeting_id: 'm-1', message: { uuid: 'm1', body_html: '<p>x</p>', created_at: '2026-09-07T10:00:00Z', author: { participant_key: 'g:1' } } });
  assert.equal(a.unreadMessages, 1);

  // My own line is not news to me.
  a.onMeetingMessage({ meeting_id: 'm-1', message: { uuid: 'm2', body_html: '<p>x</p>', created_at: '2026-09-07T10:00:00Z', author: { participant_key: 'u:7' } } });
  assert.equal(a.unreadMessages, 1);

  a.toggleChat();
  assert.equal(a.chatOpen, true);
  assert.equal(a.unreadMessages, 0);
});

test('above md the pane is on screen, so nothing is unread', () => {
  const { a } = app(okFetch, {
    matchMedia: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }),
  });
  a.meeting = MEETING;

  a.onMeetingMessage({ meeting_id: 'm-1', message: { uuid: 'm1', body_html: '<p>x</p>', created_at: '2026-09-07T10:00:00Z', author: { participant_key: 'g:1' } } });
  assert.equal(a.unreadMessages, 0);
});

test('a frame for another meeting never lands in this pane', () => {
  // The host page reads the global stream, which carries every meeting the
  // host runs - two meetings open in two tabs otherwise cross-post.
  const { a } = app(okFetch, {
    matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  });
  a.meeting = MEETING;
  const line = (uuid) => ({
    uuid, body_html: '<p>x</p>', created_at: '2026-09-07T10:00:00Z',
    author: { participant_key: 'g:1' },
  });

  a.onMeetingMessage({ meeting_id: 'another-meeting', message: line('m1') });
  assert.deepStrictEqual(Array.from(a.meetingMessages, (m) => m.uuid), []);
  assert.equal(a.unreadMessages, 0);

  a.onMeetingMessage({ meeting_id: 'm-1', message: line('m2') });
  assert.deepStrictEqual(Array.from(a.meetingMessages, (m) => m.uuid), ['m2']);
  assert.equal(a.unreadMessages, 1);

  a.onMeetingMessageDeleted({ meeting_id: 'another-meeting', message_id: 'm2' });
  assert.deepStrictEqual(Array.from(a.meetingMessages, (m) => m.uuid), ['m2']);

  a.onMeetingMessageDeleted({ meeting_id: 'm-1', message_id: 'm2' });
  assert.deepStrictEqual(Array.from(a.meetingMessages, (m) => m.uuid), []);
});

test('the host app exposes the whole call-stage surface the partial binds', () => {
  // The stage helpers live in chatCallStageMixin now; a spread order that
  // shadowed them would only surface as a blank stage in the browser.
  const { a } = app(okFetch);
  for (const name of [
    'isSpeaking', 'remoteParticipants', 'selfParticipant', 'gridColumns', 'pinTile',
    'backToGrid', 'spotlightKey', 'isSpotlight', 'spotlightParticipant',
    'stripParticipants', 'hasVideo', 'streamFor', 'onCallParticipantLeft',
    '_startDurationTimer', '_stopDurationTimer',
  ]) {
    assert.equal(typeof a[name], 'function', name);
  }
  assert.equal(a.callElapsed, '00:00');
  assert.deepStrictEqual({ ...a.speakingIds }, {});
  assert.equal(a.pinnedKey, null);
  assert.equal(a.pinnedManually, false);
});
