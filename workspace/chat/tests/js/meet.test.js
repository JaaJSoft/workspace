'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript, loadScripts } = require('../../../common/tests/js/loader');

function newStorage() {
  return {
    _d: {},
    getItem(k) { return this._d[k] ?? null; },
    setItem(k, v) { this._d[k] = String(v); },
    removeItem(k) { delete this._d[k]; },
  };
}

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
  sessionStorage: newStorage(),
  document: { getElementById: () => null, addEventListener() {}, hidden: false },
  navigator: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [] }) } },
  AbortController,
  TextDecoder,
};

function app(fetchImpl, extra = {}) {
  const ctx = loadScripts(
    [
      'workspace/chat/ui/static/chat/ui/js/call.js',
      'workspace/chat/ui/static/chat/ui/js/call_room.js',
      'workspace/chat/ui/static/chat/ui/js/meet.js',
    ],
    { ...baseStubs, sessionStorage: newStorage(), fetch: fetchImpl, ...extra },
  );
  return ctx.chatMeetApp('abc123');
}

// A fake Response body whose getReader() hands back the byte chunks the test
// wants, so the vm exercises the same decode -> parse -> dispatch path the
// browser does rather than a stand-in for it.
function bodyOf(chunks) {
  let i = 0;
  return {
    getReader() {
      return {
        read: async () => (i < chunks.length
          ? { value: chunks[i++], done: false }
          : { value: undefined, done: true }),
      };
    },
  };
}

const NL = String.fromCharCode(10);

function frame(payload, id) {
  const lines = ['event: sse'];
  if (id) lines.push('id: ' + id);
  lines.push('data: ' + JSON.stringify(payload));
  return lines.join(NL) + NL + NL;
}

function messageFrame(uuid, body, id) {
  return frame({
    event: 'message',
    data: { type: 'message', message: { uuid, body, author: { display_name: 'Bo', is_guest: false, participant_key: 'u:2' } } },
  }, id);
}

// The re-join chain continues through promises nothing awaits (the fresh
// heartbeat _startHeartbeat fires), so a test that asserts right after the
// first await sees the middle of it. Drain the queue until it stops moving.
async function settle(rounds = 30) {
  for (let i = 0; i < rounds; i += 1) await new Promise((r) => setImmediate(r));
}

function timerRecorder() {
  const delays = [];
  return { delays, setTimeout: (fn, ms) => { delays.push(ms); return delays.length; } };
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

test('a name the server rejects is reported as a missing name', async () => {
  const a = app(async () => ({ ok: false, status: 400, json: async () => ({}) }));
  a.displayName = 'x';
  await a.knock();
  assert.equal(a.error, 'Please enter a name.');
  assert.equal(a.phase, 'name');
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

test('a message is mine when its author key is my participant key', () => {
  const a = app(async () => ({ ok: true, status: 200, json: async () => ({}) }));
  a.currentParticipantKey = 'g:1';
  assert.equal(a.isOwnMessage({ author: { participant_key: 'g:1' } }), true);
  assert.equal(a.isOwnMessage({ author: { participant_key: 'g:2' } }), false);
  assert.equal(a.isOwnMessage({ author: { participant_key: 'u:5' } }), false);
  assert.equal(a.isOwnMessage(null), false);
});

// -- The event stream reader ------------------------------------------------

test('the reader dispatches each frame once, across chunk and character splits', async () => {
  const encoder = new TextEncoder();
  const wire = messageFrame('m1', 'hello', '7') + messageFrame('m2', 'café au lait', '8');
  const bytes = encoder.encode(wire);
  // Split inside the two-byte 'é' so the decoder has to carry the tail over.
  const cut = bytes.indexOf(0xc3) + 1;
  const a = app(async () => ({ ok: true, status: 200, body: bodyOf([bytes.slice(0, cut), bytes.slice(cut)]) }));
  a.token = 'tok';
  a.phase = 'lobby';

  await a._openStream();

  assert.deepStrictEqual(Array.from(a.messages, (m) => m.uuid), ['m1', 'm2']);
  assert.equal(a.messages[1].body, 'café au lait');
  assert.equal(a._lastEventId, '8');
});

test('a reconnect resumes from the last event id it saw', async () => {
  const seen = [];
  const encoder = new TextEncoder();
  let call = 0;
  const a = app(async (url, opts = {}) => {
    seen.push(opts.headers || {});
    call += 1;
    // The second connection carries an id-less frame: enough to be a healthy
    // stream rather than the zero-frame close, without moving the cursor.
    const wire = call === 1 ? messageFrame('m1', 'one', '42') : messageFrame('m2', 'two');
    return { ok: true, status: 200, body: bodyOf([encoder.encode(wire)]) };
  }, timerRecorder());
  a.token = 'tok';
  a.phase = 'lobby';

  await a._openStream();
  await a._openStream();

  assert.equal(seen[0]['Last-Event-ID'], undefined);
  assert.equal(seen[1]['Last-Event-ID'], '42');
  assert.equal(a._lastEventId, '42');
});

test('a stream that answers 200 and closes with no frames asks for the state once', async () => {
  let resumes = 0;
  const a = app(async () => ({ ok: true, status: 200, body: bodyOf([]) }), timerRecorder());
  a.token = 'tok';
  a.phase = 'lobby';
  a.resume = async () => { resumes += 1; };

  await a._openStream();

  assert.equal(resumes, 1);
});

test('a transport failure reconnects on a growing delay, and never resumes', async () => {
  const timers = timerRecorder();
  let resumes = 0;
  const a = app(async () => { throw new Error('offline'); }, timers);
  a.token = 'tok';
  a.phase = 'lobby';
  a.resume = async () => { resumes += 1; };

  await a._openStream();
  await a._openStream();

  assert.equal(resumes, 0);
  assert.deepStrictEqual(timers.delays, [1000, 2000]);
});

test('a non-2xx stream reconnects rather than treating it as an empty answer', async () => {
  const timers = timerRecorder();
  let resumes = 0;
  const a = app(async () => ({ ok: false, status: 429, body: null }), timers);
  a.token = 'tok';
  a.phase = 'lobby';
  a.resume = async () => { resumes += 1; };

  await a._openStream();

  assert.equal(resumes, 0);
  assert.deepStrictEqual(timers.delays, [1000]);
});

test('a frame resets the backoff; a bare 2xx does not', async () => {
  const timers = timerRecorder();
  const encoder = new TextEncoder();
  let call = 0;
  const a = app(async () => {
    call += 1;
    if (call === 1) throw new Error('offline');
    if (call === 2) throw new Error('offline');
    return { ok: true, status: 200, body: bodyOf([encoder.encode(messageFrame('m1', 'hi', '1'))]) };
  }, timers);
  a.token = 'tok';
  a.phase = 'lobby';

  await a._openStream();
  await a._openStream();
  await a._openStream();

  // 1000, 2000 for the two failures, then back to 1000 because a frame landed.
  assert.deepStrictEqual(timers.delays, [1000, 2000, 1000]);
});

test('a closed page reconnects nothing', async () => {
  const timers = timerRecorder();
  let resumes = 0;
  const a = app(async () => { throw new Error('offline'); }, timers);
  a.token = 'tok';
  a.phase = 'over';
  a.resume = async () => { resumes += 1; };

  await a._openStream();

  assert.deepStrictEqual(timers.delays, []);
  assert.equal(resumes, 0);
});

test('a state read that fails is retried instead of leaving a silent lobby', async () => {
  const timers = timerRecorder();
  const a = app(async () => ({ ok: false, status: 429, json: async () => ({}) }), timers);
  a.token = 'tok';
  a.phase = 'lobby';

  await a.resume();

  assert.deepStrictEqual(timers.delays, [1000]);
  assert.equal(a.phase, 'lobby');
});

// -- Leaving, reaping and a call that ends ----------------------------------

test('leaving clears the stored token so a reload cannot rejoin unattended', async () => {
  const store = newStorage();
  const a = app(async () => ({ ok: true, status: 200, json: async () => ({}) }), { sessionStorage: store });
  a.token = 'tok';
  a.callSession = { session_id: 's1' };
  a.inCall = true;
  store.setItem('meet:abc123', JSON.stringify({ token: 'tok', displayName: 'Ana', participantKey: 'g:1' }));

  await a.leaveRoom();

  assert.equal(a.phase, 'over');
  assert.equal(a.overReason, 'left');
  assert.equal(store.getItem('meet:abc123'), null);
});

test('every terminal reason clears the stored token', async () => {
  for (const reason of ['refused', 'removed', 'ended', 'left']) {
    const store = newStorage();
    const a = app(async () => ({ ok: true, status: 200, json: async () => ({}) }), { sessionStorage: store });
    store.setItem('meet:abc123', '{}');
    a.finish(reason);
    assert.equal(store.getItem('meet:abc123'), null, reason);
  }
});

test('leaveCall posts the guest leave, addressed by slug and token', async () => {
  const calls = [];
  const a = app(async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET', headers: opts.headers || {} });
    return { ok: true, status: 200, json: async () => ({}) };
  });
  a.token = 'tok';
  a.callSession = { session_id: 's1' };
  a.inCall = true;

  await a.leaveCall();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/v1/chat/meet/abc123/leave');
  assert.equal(calls[0].method, 'POST');
  assert.equal(calls[0].headers['X-Meeting-Token'], 'tok');
});

// The re-join arms a fresh heartbeat, which fires immediately: a stub that
// always refuses would recurse forever, so *reaps* bounds how many refusals
// this server hands out, and each test says what it wants to observe.
function reapedApp(reaps) {
  const seen = { captures: 0, intervals: new Set() };
  let refused = 0;
  let nextTimer = 0;
  const a = app(async (url) => {
    if (url.endsWith('/heartbeat')) {
      if (refused < reaps) { refused += 1; return { ok: false, status: 400, json: async () => ({}) }; }
      return { ok: true, status: 200, json: async () => ({}) };
    }
    if (url.endsWith('/state')) return { ok: true, status: 200, json: async () => ({ admitted: true, active: true, participants: [], participant_key: 'g:1' }) };
    if (url.endsWith('/join')) return { ok: true, status: 200, json: async () => ({ state: { active: true, session_id: 's1', participants: [] }, participant_key: 'g:1' }) };
    if (url.endsWith('/messages')) return { ok: true, status: 200, json: async () => ({ messages: [], has_more: false }) };
    return { ok: true, status: 200, json: async () => ({ title: 'T', participant_count: 0, max_participants: 8 }) };
  }, {
    setInterval: () => { nextTimer += 1; seen.intervals.add(nextTimer); return nextTimer; },
    clearInterval: (id) => { seen.intervals.delete(id); },
    navigator: { mediaDevices: { getUserMedia: async () => { seen.captures += 1; return { getTracks: () => [] }; } } },
  });
  a._openStream = () => {};
  a.token = 'tok';
  a.inCall = true;
  a.phase = 'room';
  a.callSession = { session_id: 's1' };
  return { a, seen };
}

test('a reap tears the call down before re-joining: one heartbeat, one capture', async () => {
  const { a, seen } = reapedApp(2);

  await a._sendHeartbeat();
  await settle();

  assert.equal(a.inCall, true, 'the guest is back in the call');
  assert.ok(seen.intervals.has(a._heartbeatTimer), 'the live heartbeat is the current one');
  // The two the component admits to holding - the heartbeat and the elapsed
  // ticker - and nothing left over from the round that was reaped.
  assert.equal(seen.intervals.size, 2, `orphaned intervals: ${seen.intervals.size - 2}`);
  assert.equal(seen.captures, 2, 'one microphone capture per re-join, none left over');
  assert.equal(a.phase, 'room');
});

test('a server that keeps reaping stops the re-join instead of looping', async () => {
  const { a, seen } = reapedApp(Infinity);

  await a._sendHeartbeat();
  await settle();

  assert.equal(a.inCall, false);
  assert.equal(a.phase, 'room', 'still in the room, not thrown back to the name form');
  assert.ok(seen.captures <= 3, `bounded microphone captures, got ${seen.captures}`);
  assert.ok(a.joinError);
});

test('a heartbeat answered 409 waits in the room instead of closing the page', async () => {
  const a = app(async (url) => {
    if (url.endsWith('/heartbeat')) return { ok: false, status: 409, json: async () => ({}) };
    return { ok: true, status: 200, json: async () => ({ title: 'T', participant_count: 0, max_participants: 8 }) };
  });
  a.token = 'tok';
  a.inCall = true;
  a.callSession = { session_id: 's1' };
  a.phase = 'room';

  await a._sendHeartbeat();

  assert.equal(a.phase, 'room');
  assert.equal(a.inCall, false);
  assert.equal(a.overReason, null);
});

test('a call that ends leaves the guest in the room, waiting for the next one', async () => {
  const a = app(async () => ({ ok: true, status: 200, json: async () => ({ title: 'T', participant_count: 0, max_participants: 8 }) }));
  a.token = 'tok';
  a.phase = 'room';
  a.inCall = true;
  a.callSession = { session_id: 's1' };

  await a.onCallEnded({ session_id: 's1' });

  assert.equal(a.phase, 'room');
  assert.equal(a.inCall, false);
  assert.equal(a.callSession, null);
  assert.equal(a.overReason, null);
});

test('a call_ended for another session is ignored', async () => {
  const a = app(async () => ({ ok: true, status: 200, json: async () => ({}) }));
  a.phase = 'room';
  a.inCall = true;
  a.callSession = { session_id: 's1' };

  await a.onCallEnded({ session_id: 'other' });

  assert.equal(a.inCall, true);
  assert.deepStrictEqual({ ...a.callSession }, { session_id: 's1' });
});

test('call_started is what makes a waiting guest join, with no polling', async () => {
  const urls = [];
  const a = app(async (url) => {
    urls.push(url);
    if (url.endsWith('/state')) return { ok: true, status: 200, json: async () => ({ admitted: true, active: true, session_id: 's9', participants: [], participant_key: 'g:1' }) };
    if (url.endsWith('/join')) return { ok: true, status: 200, json: async () => ({ state: { active: true, session_id: 's9', participants: [] }, participant_key: 'g:1' }) };
    return { ok: true, status: 200, json: async () => ({}) };
  });
  a.token = 'tok';
  a.phase = 'room';

  await a.onCallStarted({ session_id: 's9' });

  assert.equal(a.inCall, true);
  assert.ok(urls.includes('/api/v1/chat/meet/abc123/join'));
  assert.equal(typeof a._armCallWatch, 'undefined', 'the 8s poll is gone');
});
