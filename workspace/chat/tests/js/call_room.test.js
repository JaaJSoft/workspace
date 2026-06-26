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

test('chatCallSpotlightTarget returns the manually pinned user when still present', () => {
  const ps = [{ user_id: 1 }, { user_id: 2 }];
  assert.equal(ctx.chatCallSpotlightTarget(ps, 2, true), 2);
});

test('chatCallSpotlightTarget returns null with no pin and no sharer', () => {
  assert.equal(ctx.chatCallSpotlightTarget([{ user_id: 1 }], null, false), null);
});

test('chatCallSpotlightTarget returns null when the manually pinned user has left', () => {
  assert.equal(ctx.chatCallSpotlightTarget([{ user_id: 1 }], 99, true), null);
});

test('chatCallSpotlightTarget auto-spotlights an existing sharer for a viewer who has not pinned', () => {
  // A viewer joining mid-share derives the spotlight from live state, not from a
  // missed start-of-share event: the sharer is shown large immediately.
  const ps = [
    { user_id: 1, media_state: { audio: true } },
    { user_id: 2, media_state: { audio: true, screen: true } },
  ];
  assert.equal(ctx.chatCallSpotlightTarget(ps, null, false), 2);
});

test('chatCallSpotlightTarget clears the spotlight when the sharer stops sharing', () => {
  const sharing = [
    { user_id: 1, media_state: { audio: true } },
    { user_id: 2, media_state: { audio: true, screen: true } },
  ];
  assert.equal(ctx.chatCallSpotlightTarget(sharing, null, false), 2);
  // Same viewer state, but participant 2 stopped sharing: spotlight falls back to grid.
  const stopped = [
    { user_id: 1, media_state: { audio: true } },
    { user_id: 2, media_state: { audio: true, screen: false } },
  ];
  assert.equal(ctx.chatCallSpotlightTarget(stopped, null, false), null);
});

test('chatCallSpotlightTarget keeps a manual pin even while someone else shares', () => {
  const ps = [
    { user_id: 1, media_state: { audio: true } },
    { user_id: 2, media_state: { audio: true, screen: true } },
  ];
  assert.equal(ctx.chatCallSpotlightTarget(ps, 1, true), 1);
});

test('chatCallSpotlightTarget stays on the grid after back-to-grid even if someone shares', () => {
  // back-to-grid sets pinnedManually=true with pinnedUserId=null; auto-pin yields.
  const ps = [{ user_id: 2, media_state: { audio: true, screen: true } }];
  assert.equal(ctx.chatCallSpotlightTarget(ps, null, true), null);
});

test('chatCallAutoPinTarget picks the first active screen sharer when not manually pinned', () => {
  const ps = [
    { user_id: 1, media_state: { audio: true } },
    { user_id: 3, media_state: { audio: true, screen: true } },
  ];
  assert.equal(ctx.chatCallAutoPinTarget(ps, false), 3);
});

test('chatCallAutoPinTarget yields to a manual pin', () => {
  const ps = [{ user_id: 3, media_state: { screen: true } }];
  assert.equal(ctx.chatCallAutoPinTarget(ps, true), null);
});

test('chatCallAutoPinTarget returns null when nobody is screen sharing', () => {
  const ps = [{ user_id: 3, media_state: { audio: true, screen: false } }];
  assert.equal(ctx.chatCallAutoPinTarget(ps, false), null);
});

test('chatCallAutoPinTarget tolerates an empty or missing list', () => {
  assert.equal(ctx.chatCallAutoPinTarget([], false), null);
  assert.equal(ctx.chatCallAutoPinTarget(undefined, false), null);
});
