'use strict';
const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function mixinWithFetchRecorder() {
  const calls = [];
  const fetch = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET', headers: opts.headers || {}, body: opts.body });
    return { ok: true, status: 200, json: async () => ({ active: false, participants: [] }) };
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
  app.activeConversation = { uuid: 'conv-1' };
  app.callSession = { conversation_id: 'conv-1' };
  app.inCall = true;
  app._peers = {};
  app._mediaState = () => ({ audio: true, video: false, screen: false });
  app._teardownLocal = () => {};
  app._playCallCue = () => {};
  app._stopHeartbeat = () => {};
  app._closePeer = () => {};
  // leaveCall re-syncs the call banner for the conversation left in view;
  // that path is unrelated to the transport seam under test here and would
  // otherwise add a stray GET to `calls`.
  app._syncCallBanner = () => {};
  return { app, calls };
}

test('heartbeat targets the call conversation with CSRF and JSON headers', async () => {
  const { app, calls } = mixinWithFetchRecorder();
  await app._sendHeartbeat();
  assert.equal(calls[0].url, '/api/v1/chat/conversations/conv-1/call/heartbeat');
  assert.equal(calls[0].method, 'POST');
  assert.equal(calls[0].headers['X-CSRFToken'], 'csrf-token');
  assert.equal(calls[0].headers['Content-Type'], 'application/json');
});

test('signal targets the active conversation and carries to_participant', async () => {
  const { app, calls } = mixinWithFetchRecorder();
  await app._sendSignal('u:7', { type: 'offer' });
  assert.equal(calls[0].url, '/api/v1/chat/conversations/conv-1/call/signal');
  assert.deepStrictEqual(JSON.parse(calls[0].body), { to_participant: 'u:7', signal: { type: 'offer' } });
});

test('leave and the leave beacon POST to the call conversation with keepalive', async () => {
  const { app, calls } = mixinWithFetchRecorder();
  await app.leaveCall();
  app.callSession = { conversation_id: 'conv-1' };
  app._leaveBeacon();
  for (const c of calls) {
    assert.equal(c.url, '/api/v1/chat/conversations/conv-1/call/leave');
    assert.equal(c.method, 'POST');
    assert.equal(c.headers['X-CSRFToken'], 'csrf-token');
  }
  assert.equal(calls.length, 2);
});

test('refreshCallState GETs the call state of the active conversation', async () => {
  const { app, calls } = mixinWithFetchRecorder();
  await app._refreshCallState();
  assert.equal(calls[0].url, '/api/v1/chat/conversations/conv-1/call');
  assert.equal(calls[0].method, 'GET');
});
