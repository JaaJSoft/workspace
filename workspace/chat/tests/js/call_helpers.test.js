const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/call.js');

test('shouldOffer is true only for a different peer', () => {
  assert.equal(ctx.chatCallShouldOffer(1, 2), true);
  assert.equal(ctx.chatCallShouldOffer(2, 2), false);
});

test('mergeMediaState overlays patch onto current', () => {
  const merged = ctx.chatCallMergeMediaState({ audio: true }, { audio: false });
  assert.deepStrictEqual({ ...merged }, { audio: false });
  const added = ctx.chatCallMergeMediaState({ audio: true }, { screen: true });
  assert.deepStrictEqual({ ...added }, { audio: true, screen: true });
});

test('otherParticipantIds excludes self', () => {
  const ids = ctx.chatCallOtherParticipantIds(
    [{ user_id: 1 }, { user_id: 2 }, { user_id: 3 }],
    2,
  );
  assert.deepStrictEqual(Array.from(ids), [1, 3]);
});

test('chatCallEventForCurrentSession returns true for matching session_id', () => {
  const session = { session_id: 'abc-123' };
  const detail = { session_id: 'abc-123' };
  assert.equal(ctx.chatCallEventForCurrentSession(detail, session), true);
});

test('chatCallEventForCurrentSession returns false for different session_id', () => {
  const session = { session_id: 'abc-123' };
  const detail = { session_id: 'xyz-999' };
  assert.equal(ctx.chatCallEventForCurrentSession(detail, session), false);
});

test('chatCallEventForCurrentSession returns false when callSession is null', () => {
  const detail = { session_id: 'abc-123' };
  assert.equal(ctx.chatCallEventForCurrentSession(detail, null), false);
});

test('chatCallEventForCurrentSession returns false when detail is null or undefined', () => {
  const session = { session_id: 'abc-123' };
  assert.equal(ctx.chatCallEventForCurrentSession(null, session), false);
  assert.equal(ctx.chatCallEventForCurrentSession(undefined, session), false);
});

test('startOrJoinCall delegates to the room for an observer (no media capture)', () => {
  const callCtx = loadScript('workspace/chat/ui/static/chat/ui/js/call.js', {
    chatCallShouldOwnMedia: (r) => r !== 'observer',
  });
  const m = callCtx.chatCallMixin();
  let openedWith = null;
  m.openCallRoom = (id) => { openedWith = id; };
  m.callRole = 'observer';
  m.activeConversation = { uuid: 'conv-9' };
  m.startOrJoinCall();
  assert.equal(openedWith, 'conv-9');     // observer delegates to the room tab
  assert.equal(m.joiningCall, false);     // never entered the media/join flow
});

test('chatCallMediaState maps mic/camera/screen flags', () => {
  assert.deepEqual(ctx.chatCallMediaState(false, false, false), { audio: true, video: false, screen: false });
  assert.deepEqual(ctx.chatCallMediaState(true, true, false), { audio: false, video: true, screen: false });
  assert.deepEqual(ctx.chatCallMediaState(false, false, true), { audio: true, video: false, screen: true });
});

test('shouldDriveIceRestart picks the lower-id peer as the single driver', () => {
  // Deterministic glare avoidance for mid-call restarts: both peers are
  // existing participants, so the lower user_id drives. Exactly one side does.
  assert.equal(ctx.chatCallShouldDriveIceRestart(1, 2), true);
  assert.equal(ctx.chatCallShouldDriveIceRestart(2, 1), false);
  assert.equal(ctx.chatCallShouldDriveIceRestart(2, 2), false);
});

test('iceRestartDelay applies grace on disconnected and backoff by attempt', () => {
  // failed: immediate first attempt, then exponential backoff 0 -> 2000 -> 4000.
  assert.equal(ctx.chatCallIceRestartDelay('failed', 0), 0);
  assert.equal(ctx.chatCallIceRestartDelay('failed', 1), 2000);
  assert.equal(ctx.chatCallIceRestartDelay('failed', 2), 4000);
  // disconnected: a 3s grace floor that often lets the state self-recover.
  assert.equal(ctx.chatCallIceRestartDelay('disconnected', 0), 3000);
  assert.equal(ctx.chatCallIceRestartDelay('disconnected', 1), 3000);
  assert.equal(ctx.chatCallIceRestartDelay('disconnected', 2), 4000);
});

// _scheduleIceRestart bookkeeping. The method calls setTimeout/clearTimeout,
// so the script is loaded with fake timers injected as context globals; the
// pc is a plain stub (no RTCPeerConnection needed for the scheduling logic).
function scheduleHarness() {
  const timers = [];
  const cleared = [];
  let nextId = 0;
  const callCtx = loadScript('workspace/chat/ui/static/chat/ui/js/call.js', {
    setTimeout: (fn, delay) => { const id = ++nextId; timers.push({ id, fn, delay }); return id; },
    clearTimeout: (id) => { cleared.push(id); },
  });
  const m = callCtx.chatCallMixin();
  m.currentUserId = 1;             // peer 2 -> currentUserId < peerId -> we drive
  m._performIceRestart = () => {}; // isolate scheduling from the restart itself
  m._peers = { 2: { pc: { iceConnectionState: 'disconnected' }, iceRestartAttempts: 0, iceRestartTimer: null } };
  return { m, timers, cleared };
}

test('scheduleIceRestart: failed cancels a pending disconnected grace timer and restarts immediately', () => {
  const { m, timers, cleared } = scheduleHarness();
  // disconnected -> a 3s grace timer is armed.
  m._scheduleIceRestart(2);
  assert.equal(timers.length, 1);
  assert.equal(timers[0].delay, 3000);
  const graceId = m._peers[2].iceRestartTimer;

  // The connection then fails: the grace timer must be cancelled and an
  // immediate (0ms) restart scheduled instead of waiting out the grace.
  m._peers[2].pc.iceConnectionState = 'failed';
  m._scheduleIceRestart(2);
  assert.deepStrictEqual(cleared, [graceId]);
  assert.equal(timers.length, 2);
  assert.equal(timers[1].delay, 0);
});

test('scheduleIceRestart: a repeated disconnected keeps the existing timer (debounce)', () => {
  const { m, timers, cleared } = scheduleHarness();
  m._scheduleIceRestart(2);
  const firstId = m._peers[2].iceRestartTimer;
  // Still disconnected: do not stack a second timer.
  m._scheduleIceRestart(2);
  assert.equal(timers.length, 1);
  assert.deepStrictEqual(cleared, []);
  assert.equal(m._peers[2].iceRestartTimer, firstId);
});
