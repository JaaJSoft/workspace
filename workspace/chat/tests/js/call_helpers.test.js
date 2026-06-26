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
