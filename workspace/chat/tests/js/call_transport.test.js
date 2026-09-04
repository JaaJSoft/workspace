'use strict';
const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// activeConversation (the conversation being viewed) and callSession (the
// call actually joined) are deliberately different conversations, matching
// the "browsing elsewhere while in a call" scenario the mixin supports. A
// site that reads the wrong one still produces a URL, so the test must
// assert the id, not just the shape.
const VIEWED_CONV_ID = 'conv-viewed';
const CALL_CONV_ID = 'conv-call';

function mixinWithFetchRecorder(jsonResponse = { active: false, participants: [] }) {
  const calls = [];
  const fetch = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET', headers: opts.headers || {}, body: opts.body });
    return { ok: true, status: 200, json: async () => jsonResponse };
  };
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/call.js', {
    fetch,
    getCSRFToken: () => 'csrf-token',
    navigator: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [] }) } },
    document: { getElementById: () => null, addEventListener() {} },
    AppAlert: { error() {} },
    chatCallShouldOwnMedia: () => true,
  });
  const app = ctx.chatCallMixin();
  app.activeConversation = { uuid: VIEWED_CONV_ID };
  app.callSession = { conversation_id: CALL_CONV_ID };
  app.inCall = true;
  app._peers = {};
  app._mediaState = () => ({ audio: true, video: false, screen: false });
  app._teardownLocal = () => {};
  app._playCallCue = () => {};
  app._stopHeartbeat = () => {};
  app._closePeer = () => {};
  // leaveCall re-syncs the call banner for the conversation left in view.
  // That call reaches _refreshCallState through this same transport seam,
  // which the state test below already pins; stubbing it here just keeps
  // the leave test's `calls` free of a GET the leave assertions don't care
  // about.
  app._syncCallBanner = () => {};
  return { app, calls };
}

test('heartbeat targets the call conversation with CSRF and JSON headers', async () => {
  const { app, calls } = mixinWithFetchRecorder();
  await app._sendHeartbeat();
  assert.equal(calls[0].url, `/api/v1/chat/conversations/${CALL_CONV_ID}/call/heartbeat`);
  assert.equal(calls[0].method, 'POST');
  assert.equal(calls[0].headers['X-CSRFToken'], 'csrf-token');
  assert.equal(calls[0].headers['Content-Type'], 'application/json');
});

test('signal targets the viewed conversation and carries to_participant', async () => {
  const { app, calls } = mixinWithFetchRecorder();
  await app._sendSignal('u:7', { type: 'offer' });
  assert.equal(calls[0].url, `/api/v1/chat/conversations/${VIEWED_CONV_ID}/call/signal`);
  assert.deepStrictEqual(JSON.parse(calls[0].body), { to_participant: 'u:7', signal: { type: 'offer' } });
});

test('leave and the leave beacon POST to the call conversation with keepalive', async () => {
  const { app, calls } = mixinWithFetchRecorder();
  await app.leaveCall();
  app.callSession = { conversation_id: CALL_CONV_ID };
  app._leaveBeacon();
  for (const c of calls) {
    assert.equal(c.url, `/api/v1/chat/conversations/${CALL_CONV_ID}/call/leave`);
    assert.equal(c.method, 'POST');
    assert.equal(c.headers['X-CSRFToken'], 'csrf-token');
  }
  assert.equal(calls.length, 2);
});

test('refreshCallState GETs the call state of the viewed conversation', async () => {
  const { app, calls } = mixinWithFetchRecorder();
  await app._refreshCallState();
  assert.equal(calls[0].url, `/api/v1/chat/conversations/${VIEWED_CONV_ID}/call`);
  assert.equal(calls[0].method, 'GET');
});

test('startOrJoinCall POSTs the join request to the viewed conversation', async () => {
  const { app, calls } = mixinWithFetchRecorder({
    ice_servers: [],
    state: { conversation_id: VIEWED_CONV_ID, session_id: 's1', participants: [] },
  });
  app.inCall = false;
  app.joiningCall = false;
  // Zero participants in the join response means the post-join peer loop
  // (window.chatCallOtherParticipantIds) is empty, so _ensurePeer (real
  // WebRTC wiring, not part of this transport seam) is never reached.
  app._startHeartbeat = () => {};
  await app.startOrJoinCall();
  assert.equal(calls[0].url, `/api/v1/chat/conversations/${VIEWED_CONV_ID}/call/join`);
  assert.equal(calls[0].method, 'POST');
  assert.equal(calls[0].headers['X-CSRFToken'], 'csrf-token');
  assert.equal(calls[0].headers['Content-Type'], 'application/json');
});
