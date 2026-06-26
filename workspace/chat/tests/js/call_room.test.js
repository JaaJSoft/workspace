const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/call_room.js');

test('chatCallRoomUrl builds the room path', () => {
  assert.equal(ctx.chatCallRoomUrl('abc'), '/chat/room/abc');
});

test('chatCallRoomTabName builds a deterministic tab name', () => {
  assert.equal(ctx.chatCallRoomTabName('abc'), 'chat-room-abc');
});

test('chatCallBannerAction returns null when no active call', () => {
  assert.equal(ctx.chatCallBannerAction(false, [], 1), null);
});

test('chatCallBannerAction returns join when I am not a participant', () => {
  assert.equal(
    ctx.chatCallBannerAction(true, [{ user_id: 2 }], 1),
    'join',
  );
});

test('chatCallBannerAction returns return when I am a participant', () => {
  assert.equal(
    ctx.chatCallBannerAction(true, [{ user_id: 1 }, { user_id: 2 }], 1),
    'return',
  );
});

test('chatIsSpeaking compares level against the threshold', () => {
  assert.equal(ctx.chatIsSpeaking(0.2, 0.05), true);
  assert.equal(ctx.chatIsSpeaking(0.01, 0.05), false);
  assert.equal(ctx.chatIsSpeaking(0.2), true); // default threshold
  assert.equal(ctx.chatIsSpeaking(null), false);
});

test('chatCallBannerAction returns join when participants is null', () => {
  assert.equal(ctx.chatCallBannerAction(true, null, 1), 'join');
});

test('chatIsSpeaking returns true at the threshold boundary', () => {
  assert.equal(ctx.chatIsSpeaking(0.05, 0.05), true);
});

test('chatCallShouldOwnMedia is false only for observer', () => {
  assert.equal(ctx.chatCallShouldOwnMedia('owner'), true);
  assert.equal(ctx.chatCallShouldOwnMedia('observer'), false);
  assert.equal(ctx.chatCallShouldOwnMedia(undefined), true);
});

test('chatCallSpotlightTarget returns the pinned user when still present', () => {
  const ps = [{ user_id: 1 }, { user_id: 2 }];
  assert.equal(ctx.chatCallSpotlightTarget(ps, 2), 2);
});

test('chatCallSpotlightTarget returns null when nothing is pinned', () => {
  assert.equal(ctx.chatCallSpotlightTarget([{ user_id: 1 }], null), null);
});

test('chatCallSpotlightTarget returns null when the pinned user has left', () => {
  assert.equal(ctx.chatCallSpotlightTarget([{ user_id: 1 }], 99), null);
});

test('chatCallAutoPinTarget pins a fresh screen sharer when not manually pinned', () => {
  const p = { user_id: 3, media_state: { audio: true, screen: true } };
  assert.equal(ctx.chatCallAutoPinTarget(p, false), 3);
});

test('chatCallAutoPinTarget yields to a manual pin', () => {
  const p = { user_id: 3, media_state: { screen: true } };
  assert.equal(ctx.chatCallAutoPinTarget(p, true), null);
});

test('chatCallAutoPinTarget ignores a participant who is not screen sharing', () => {
  const p = { user_id: 3, media_state: { audio: true, screen: false } };
  assert.equal(ctx.chatCallAutoPinTarget(p, false), null);
});
