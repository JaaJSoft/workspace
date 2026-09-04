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

// Records what was scheduled and what was cancelled, and keeps the callbacks
// so a test can fire a pending timer by hand.
function timerRecorder() {
  const delays = [];
  const fns = [];
  const cleared = [];
  return {
    delays,
    fns,
    cleared,
    setTimeout: (fn, ms) => { delays.push(ms); fns.push(fn); return fns.length; },
    clearTimeout: (id) => { cleared.push(id); },
  };
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

test('a guest waiting for a call can still leave the meeting', async () => {
  const calls = [];
  const store = newStorage();
  const a = app(async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    return { ok: true, status: 200, json: async () => ({}) };
  }, { sessionStorage: store });
  a.token = 'tok';
  a.phase = 'room';
  a.inCall = false;
  a.callSession = null;
  store.setItem('meet:abc123', '{}');

  await a.leaveRoom();

  assert.equal(a.phase, 'over');
  assert.equal(a.overReason, 'left');
  assert.equal(store.getItem('meet:abc123'), null);
  // The endpoint answers 200 for an admitted guest with no participant row,
  // so telling it is free and keeps the two paths identical.
  assert.deepStrictEqual(
    calls.map((c) => c.url),
    ['/api/v1/chat/meet/abc123/leave'],
  );
});

test('the leave beacon fires for a guest holding a token but no call', () => {
  const calls = [];
  const a = app(async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    return { ok: true, status: 200, json: async () => ({}) };
  });
  a.token = 'tok';
  a.callSession = null;

  a._leaveBeacon();

  assert.deepStrictEqual(calls.map((c) => c.url), ['/api/v1/chat/meet/abc123/leave']);
});

test('a pending retry cannot reopen a page the guest has closed', async () => {
  const timers = timerRecorder();
  let fetches = 0;
  const a = app(async () => { fetches += 1; return { ok: false, status: 500, json: async () => ({}) }; }, timers);
  a.token = 'tok';
  a.phase = 'lobby';

  await a.resume();
  assert.equal(timers.fns.length, 1, 'a retry is pending');
  const fetchesBefore = fetches;

  a.finish('left');
  await timers.fns[0]();
  await settle(3);

  assert.equal(a.phase, 'over', 'the closed page stays closed');
  assert.equal(a.overReason, 'left');
  assert.equal(fetches, fetchesBefore, 'the timer that fired asked the server nothing');
  assert.ok(timers.cleared.length >= 1, 'finish() cancelled the pending retry');
});

test('waitForCall cannot reopen a page the guest has closed', async () => {
  const a = app(async () => ({ ok: true, status: 200, json: async () => ({}) }));
  a.token = 'tok';
  a.finish('ended');

  await a.waitForCall('anything');

  assert.equal(a.phase, 'over');
  assert.equal(a.overReason, 'ended');
});

test('only one retry is ever pending, so one backoff governs one timer', async () => {
  const timers = timerRecorder();
  const a = app(async () => ({ ok: false, status: 500, json: async () => ({}) }), timers);
  a.token = 'tok';
  a.phase = 'lobby';

  await a.resume();
  await a.resume();

  assert.equal(timers.fns.length, 2, 'both attempts scheduled');
  assert.deepStrictEqual(timers.cleared, [1], 'the first handle was cancelled before the second');
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

test('a locked meeting refuses the join and parks the guest with a retry', async () => {
  const a = app(async (url) => {
    if (url.endsWith('/state')) return { ok: true, status: 200, json: async () => ({ admitted: true, active: true, session_id: 's1', participants: [], participant_key: 'g:1' }) };
    // The guest join answers 423 with no body at all.
    if (url.endsWith('/join')) return { ok: false, status: 423, json: async () => { throw new SyntaxError('Unexpected end of JSON input'); } };
    return { ok: true, status: 200, json: async () => ({ title: 'T', participant_count: 0, max_participants: 8 }) };
  });
  a._openStream = () => {};
  a.token = 'tok';
  a.phase = 'room';

  await a.joinWhenCallStarts();

  assert.equal(a.inCall, false);
  assert.equal(a.joiningCall, false, 'the join is released, so Try again can work');
  assert.equal(a.phase, 'room', 'parked in the room, not thrown out');
  assert.match(a.joinError, /locked/i);
  assert.equal(a._streamAbort, null, 'no half-open stream left behind');
});

test('a join refused 404 re-reads the state instead of guessing', async () => {
  const urls = [];
  let states = 0;
  const a = app(async (url) => {
    urls.push(url);
    if (url.endsWith('/join')) return { ok: false, status: 404, json: async () => { throw new SyntaxError('no body'); } };
    if (url.endsWith('/state')) {
      states += 1;
      // The gate join failed is the gate state answers on, so a token join
      // refuses is a token state stops knowing.
      if (states === 1) return { ok: true, status: 200, json: async () => ({ admitted: true, active: true, session_id: 's1', participants: [], participant_key: 'g:1' }) };
      return { ok: false, status: 404, json: async () => ({}) };
    }
    return { ok: true, status: 200, json: async () => ({ messages: [], has_more: false }) };
  });
  a._openStream = () => {};
  a.token = 'tok';
  a.phase = 'room';

  await a.joinWhenCallStarts();
  await settle(3);

  assert.ok(urls.filter((u) => u.endsWith('/state')).length >= 2, 'it asked the server again');
  assert.equal(a.phase, 'name', 'a token nobody knows sends the guest back to the door');
});

test('a 409 refusal shows what the server said, not a made-up reason', async () => {
  const a = app(async (url) => {
    if (url.endsWith('/state')) return { ok: true, status: 200, json: async () => ({ admitted: true, active: true, session_id: 's1', participants: [], participant_key: 'g:1' }) };
    if (url.endsWith('/join')) return { ok: false, status: 409, json: async () => ({ detail: 'Call is full.' }) };
    return { ok: true, status: 200, json: async () => ({ title: 'T', participant_count: 8, max_participants: 8 }) };
  });
  a._openStream = () => {};
  a.token = 'tok';
  a.phase = 'room';

  await a.joinWhenCallStarts();

  assert.equal(a.joinError, 'Call is full.');
});

test('a keepalive-only connection reconnects instead of asking for the state', async () => {
  const timers = timerRecorder();
  const encoder = new TextEncoder();
  let resumes = 0;
  const a = app(async () => ({ ok: true, status: 200, body: bodyOf([encoder.encode(':keepalive' + NL + NL)]) }), timers);
  a.token = 'tok';
  a.phase = 'lobby';
  a.resume = async () => { resumes += 1; };
  a._streamBackoffMs = 8000;

  await a._openStream();

  assert.equal(resumes, 0, 'bytes arrived: the server is talking, nothing to re-ask');
  assert.deepStrictEqual(timers.delays, [1000], 'and the backoff is back to its first rung');
});

test('messages that arrive with the chat panel closed are counted, and reading clears it', () => {
  const a = app(async () => ({ ok: true, status: 200, json: async () => ({}) }));
  a.currentParticipantKey = 'g:1';

  a.onIncomingMessage({ uuid: 'm1', author: { participant_key: 'u:2' } });
  a.onIncomingMessage({ uuid: 'm2', author: { participant_key: 'u:2' } });
  assert.equal(a.unreadMessages, 2);

  // My own message is not news to me.
  a.onIncomingMessage({ uuid: 'm3', author: { participant_key: 'g:1' } });
  assert.equal(a.unreadMessages, 2);

  a.toggleChat();
  assert.equal(a.chatOpen, true);
  assert.equal(a.unreadMessages, 0);

  a.onIncomingMessage({ uuid: 'm4', author: { participant_key: 'u:2' } });
  assert.equal(a.unreadMessages, 0, 'nothing is unread while the panel is open');

  a.toggleChat();
  a.onIncomingMessage({ uuid: 'm5', author: { participant_key: 'u:2' } });
  assert.equal(a.unreadMessages, 1);
});

// A server whose /join refuses with a bodyless 404 while its /state keeps
// answering "admitted, and there is a call" is inconsistent - the two share
// one gate, so it cannot happen in production. The client must not spin on it
// anyway: each round costs a microphone capture. The stub relents after
// RELENT_AFTER refusals so a lost bound fails an assertion instead of hanging
// the runner.
const RELENT_AFTER = 6;

function stubbornlyRefusingApp(extra = {}) {
  const seen = { joins: 0, captures: 0, stopped: 0 };
  const a = app(async (url) => {
    if (url.endsWith('/join')) {
      seen.joins += 1;
      if (seen.joins <= RELENT_AFTER) {
        return { ok: false, status: 404, json: async () => { throw new SyntaxError('no body'); } };
      }
      return { ok: true, status: 200, json: async () => ({ state: { active: true, session_id: 's1', participants: [] }, participant_key: 'g:1' }) };
    }
    if (url.endsWith('/state')) return { ok: true, status: 200, json: async () => ({ admitted: true, active: true, session_id: 's1', participants: [], participant_key: 'g:1' }) };
    if (url.endsWith('/messages')) return { ok: true, status: 200, json: async () => ({ messages: [], has_more: false }) };
    return { ok: true, status: 200, json: async () => ({ title: 'T', participant_count: 0, max_participants: 8 }) };
  }, {
    navigator: {
      mediaDevices: {
        getUserMedia: async () => {
          seen.captures += 1;
          return { getTracks: () => [{ stop() { seen.stopped += 1; }, enabled: true }] };
        },
      },
    },
    ...extra,
  });
  a._openStream = () => {};
  a.token = 'tok';
  a.phase = 'room';
  return { a, seen };
}

test('a join that keeps answering 404 is retried once, then parked', async () => {
  const timers = timerRecorder();
  const { a, seen } = stubbornlyRefusingApp(timers);

  await a.joinWhenCallStarts();
  await settle();

  assert.equal(seen.joins, 2, 'one attempt, one automatic retry, then it stops asking');
  assert.equal(seen.captures, 2, 'a microphone capture per attempt, and no more');
  assert.equal(seen.stopped, 2, 'both captures released');
  assert.equal(a.inCall, false);
  assert.equal(a.phase, 'room', 'parked in the room with a Try again, not thrown out');
  assert.ok(a.joinError, 'and told why');
  assert.deepStrictEqual(timers.delays, [], 'nothing pending to wake it back into the loop');
  assert.equal(a._retryTimer, null);
});

test('a later call_started gets its automatic retry back', async () => {
  const { a, seen } = stubbornlyRefusingApp();

  await a.joinWhenCallStarts();
  await settle();
  assert.equal(seen.joins, 2);

  // The host starts another call: a fresh attempt, with a fresh budget - one
  // transient refusal must not cost the automatic retry for the whole session.
  await a.onCallStarted({ session_id: 's2' });
  await settle();

  assert.equal(seen.joins, 4, 'the second attempt also gets one automatic retry');
  assert.equal(a.phase, 'room');
});
