'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript, loadScripts } = require('../../../common/tests/js/loader');

// The guest page runs outside the app shell, so the vm gets the handful of
// browser globals meet.js and the call mixin reach for. setInterval is one of
// them: joining a call arms the heartbeat.
const baseStubs = {
  getCSRFToken: () => '',
  AppAlert: { error() {}, warning() {}, success() {} },
  chatCallShouldOwnMedia: () => true,
  setInterval: () => 0,
  clearInterval: () => {},
  setTimeout: () => 0,
  sessionStorage: {
    _d: {},
    getItem(k) { return this._d[k] ?? null; },
    setItem(k, v) { this._d[k] = String(v); },
    removeItem(k) { delete this._d[k]; },
  },
  document: { getElementById: () => null, addEventListener() {}, hidden: false },
  navigator: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [] }) } },
};

function app(fetchImpl) {
  const ctx = loadScripts(
    [
      'workspace/chat/ui/static/chat/ui/js/call.js',
      'workspace/chat/ui/static/chat/ui/js/call_room.js',
      'workspace/chat/ui/static/chat/ui/js/meet.js',
    ],
    { ...baseStubs, fetch: fetchImpl },
  );
  return ctx.chatMeetApp('abc123');
}

test('parseSseChunk splits complete frames and keeps the remainder', () => {
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/meet.js', baseStubs);
  const { frames, rest } = ctx.chatMeetParseSseChunk(
    'event: sse\nid: 11\ndata: {"event":"message","data":{"a":1}}\n\nevent: sse\ndata: {"ev',
  );
  assert.equal(frames.length, 1);
  assert.equal(frames[0].id, '11');
  assert.equal(frames[0].payload.event, 'message');
  assert.deepStrictEqual({ ...frames[0].payload.data }, { a: 1 });
  assert.equal(rest, 'event: sse\ndata: {"ev');
});

test('parseSseChunk skips the keepalive comment frame', () => {
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/meet.js', baseStubs);
  const { frames, rest } = ctx.chatMeetParseSseChunk(':keepalive\n\n');
  assert.equal(frames.length, 0);
  assert.equal(rest, '');
});

test('guest transport targets the meet endpoints with the token header', async () => {
  const a = app(async () => ({ ok: true, status: 200, json: async () => ({}) }));
  a.token = 'tok';
  assert.equal(a._callEndpoint('join', 'ignored'), '/api/v1/chat/meet/abc123/join');
  assert.equal(a._callEndpoint('', 'ignored'), '/api/v1/chat/meet/abc123/state');
  assert.deepStrictEqual(
    { ...a._callHeaders({ json: true }) },
    { 'X-Meeting-Token': 'tok', 'Content-Type': 'application/json' },
  );
});

test('knock moves to the lobby and stores the token; admission then joins', async () => {
  const a = app(async (url) => {
    if (url.endsWith('/knock')) return { ok: true, status: 201, json: async () => ({ token: 'tok', state: 'waiting', display_name: 'Ana', participant_key: 'g:1' }) };
    if (url.endsWith('/state')) return { ok: true, status: 200, json: async () => ({ admitted: true, active: true, participants: [], participant_key: 'g:1', max_participants: 6 }) };
    if (url.endsWith('/join')) return { ok: true, status: 200, json: async () => ({ state: { active: true, participants: [], max_participants: 6 }, participant_key: 'g:1' }) };
    if (url.endsWith('/messages')) return { ok: true, status: 200, json: async () => ({ messages: [], has_more: false }) };
    return { ok: true, status: 200, json: async () => ({}) };
  });
  a._openStream = () => {};
  a.displayName = 'Ana';
  await a.knock();
  assert.equal(a.phase, 'lobby');
  assert.equal(a.token, 'tok');
  assert.equal(a.currentParticipantKey, 'g:1');
  await a.onMeetingEvent({ event: 'meeting_admitted', data: {} });
  assert.equal(a.phase, 'room');
  assert.equal(a.inCall, true);
});

test('refused, removed and ended each close the page with the right reason', async () => {
  for (const [event, reason] of [['meeting_refused', 'refused'], ['meeting_removed', 'removed'], ['meeting_ended', 'ended']]) {
    const a = app(async () => ({ ok: true, status: 200, json: async () => ({}) }));
    a._openStream = () => {}; a._closeStream = () => {}; a.leaveCall = async () => {};
    a.phase = 'room';
    await a.onMeetingEvent({ event, data: {} });
    assert.equal(a.phase, 'over');
    assert.equal(a.overReason, reason);
  }
});

test('a heartbeat the sweep already reaped re-joins instead of going quiet', async () => {
  const urls = [];
  const a = app(async (url) => {
    urls.push(url);
    if (url.endsWith('/heartbeat')) return { ok: false, status: 400, json: async () => ({}) };
    if (url.endsWith('/state')) return { ok: true, status: 200, json: async () => ({ admitted: true, active: false, participant_key: 'g:1' }) };
    if (url.endsWith('/messages')) return { ok: true, status: 200, json: async () => ({ messages: [], has_more: false }) };
    if (url.endsWith('/join')) return { ok: true, status: 200, json: async () => ({ state: { active: true, participants: [] }, participant_key: 'g:1' }) };
    return { ok: true, status: 200, json: async () => ({}) };
  });
  a._openStream = () => {};
  a.token = 'tok';
  a.inCall = true;
  await a._sendHeartbeat();
  assert.ok(urls.includes('/api/v1/chat/meet/abc123/state'), 'the reaped guest asks for its own state');
  assert.equal(a.phase, 'room');
});

test('the stage helpers split self from remote on the participant key', () => {
  const a = app(async () => ({ ok: true, status: 200, json: async () => ({}) }));
  a.currentParticipantKey = 'g:1';
  a.callParticipants = [
    { participant_key: 'g:1', display_name: 'Ana', media_state: {} },
    { participant_key: 'u:2', display_name: 'Bo', media_state: { screen: true } },
  ];
  assert.deepStrictEqual(a.remoteParticipants().map((p) => p.participant_key), ['u:2']);
  assert.equal(a.selfParticipant().display_name, 'Ana');
  // The screen sharer is auto-spotlighted through the same call_room.js
  // helper the member room uses.
  assert.equal(a.spotlightKey(), 'u:2');
  assert.deepStrictEqual(a.stripParticipants().map((p) => p.participant_key), ['g:1']);
});
