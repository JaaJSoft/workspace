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
  assert.equal(ctx.chatCallBannerAction(false, [], 'u:1'), null);
});

test('chatCallBannerAction returns join when I am not a participant', () => {
  assert.equal(
    ctx.chatCallBannerAction(true, [{ participant_key: 'u:2' }], 'u:1'),
    'join',
  );
});

test('chatCallBannerAction returns return when I am a participant', () => {
  assert.equal(
    ctx.chatCallBannerAction(true, [{ participant_key: 'u:1' }, { participant_key: 'u:2' }], 'u:1'),
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
  assert.equal(ctx.chatCallBannerAction(true, null, 'u:1'), 'join');
});

test('chatIsSpeaking returns true at the threshold boundary', () => {
  assert.equal(ctx.chatIsSpeaking(0.05, 0.05), true);
});

test('chatCallShouldOwnMedia is false only for observer', () => {
  assert.equal(ctx.chatCallShouldOwnMedia('owner'), true);
  assert.equal(ctx.chatCallShouldOwnMedia('observer'), false);
  assert.equal(ctx.chatCallShouldOwnMedia(undefined), true);
});

test('chatCallBannerAction distinguishes a participant from an observer', () => {
  const ps = [{ participant_key: 'u:1' }, { participant_key: 'u:2' }];
  assert.equal(ctx.chatCallBannerAction(true, ps, 'u:2'), 'return');
  assert.equal(ctx.chatCallBannerAction(true, ps, 'u:9'), 'join');
  assert.equal(ctx.chatCallBannerAction(false, ps, 'u:2'), null);
});

test('chatCallSpotlightTarget returns the manually pinned participant when still present', () => {
  const ps = [{ participant_key: 'u:1' }, { participant_key: 'u:2' }];
  assert.equal(ctx.chatCallSpotlightTarget(ps, 'u:2', true), 'u:2');
});

test('chatCallSpotlightTarget returns null with no pin and no sharer', () => {
  assert.equal(ctx.chatCallSpotlightTarget([{ participant_key: 'u:1' }], null, false), null);
});

test('chatCallSpotlightTarget returns null when the manually pinned participant has left', () => {
  assert.equal(ctx.chatCallSpotlightTarget([{ participant_key: 'u:1' }], 'u:99', true), null);
});

test('chatCallSpotlightTarget auto-spotlights a sharing guest for a viewer who has not pinned', () => {
  // A viewer joining mid-share derives the spotlight from live state, not from a
  // missed start-of-share event: the sharer is shown large immediately.
  const ps = [
    { participant_key: 'u:1', media_state: { screen: false } },
    { participant_key: 'g:abc', media_state: { screen: true } },
  ];
  assert.equal(ctx.chatCallSpotlightTarget(ps, null, false), 'g:abc');
});

test('chatCallSpotlightTarget clears the spotlight when the sharer stops sharing', () => {
  const sharing = [{ participant_key: 'u:2', media_state: { screen: true } }];
  const stopped = [{ participant_key: 'u:2', media_state: { screen: false } }];
  assert.equal(ctx.chatCallSpotlightTarget(sharing, null, false), 'u:2');
  // Same viewer state, but participant u:2 stopped sharing: spotlight falls back to grid.
  assert.equal(ctx.chatCallSpotlightTarget(stopped, null, false), null);
});

test('chatCallSpotlightTarget keeps a manual pin even while someone else shares', () => {
  const ps = [
    { participant_key: 'u:1', media_state: { audio: true } },
    { participant_key: 'u:2', media_state: { audio: true, screen: true } },
  ];
  assert.equal(ctx.chatCallSpotlightTarget(ps, 'u:1', true), 'u:1');
});

test('chatCallSpotlightTarget stays on the grid after back-to-grid even if someone shares', () => {
  // back-to-grid sets pinnedManually=true with pinnedKey=null; auto-pin yields.
  const ps = [{ participant_key: 'u:2', media_state: { audio: true, screen: true } }];
  assert.equal(ctx.chatCallSpotlightTarget(ps, null, true), null);
});

test('chatCallAutoPinTarget picks the first active screen sharer when not manually pinned', () => {
  const ps = [
    { participant_key: 'u:1', media_state: { audio: true } },
    { participant_key: 'u:3', media_state: { audio: true, screen: true } },
  ];
  assert.equal(ctx.chatCallAutoPinTarget(ps, false), 'u:3');
});

test('chatCallAutoPinTarget yields to a manual pin', () => {
  const ps = [{ participant_key: 'u:3', media_state: { screen: true } }];
  assert.equal(ctx.chatCallAutoPinTarget(ps, true), null);
});

test('chatCallAutoPinTarget returns null when nobody is screen sharing', () => {
  const ps = [{ participant_key: 'u:3', media_state: { audio: true, screen: false } }];
  assert.equal(ctx.chatCallAutoPinTarget(ps, false), null);
});

test('chatCallAutoPinTarget tolerates an empty or missing list', () => {
  assert.equal(ctx.chatCallAutoPinTarget([], false), null);
  assert.equal(ctx.chatCallAutoPinTarget(undefined, false), null);
});

// -- chatCallStageMixin -----------------------------------------------------
// The tile split, the spotlight and the elapsed clock the three pages used to
// carry verbatim copies of. room.test.js and meet.test.js reach a few of these
// through their components; the ones below had no test at all.

const { loadScripts } = require('../../../common/tests/js/loader');

function stage(overrides = {}, globals = {}) {
  // call.js rides along for chatCallEventForCurrentSession, the session guard
  // onCallParticipantLeft consults.
  const stageCtx = loadScripts(
    [
      'workspace/chat/ui/static/chat/ui/js/call.js',
      'workspace/chat/ui/static/chat/ui/js/call_room.js',
    ],
    globals,
  );
  return Object.assign(stageCtx.chatCallStageMixin(), {
    currentParticipantKey: 'u:1',
    callParticipants: [],
    callSession: null,
    inCall: false,
    cameraOn: false,
    sharing: false,
    localVideoStream: null,
    remoteStreams: {},
    _closePeer() {},
    _playCallCue() {},
    ...overrides,
  });
}

test('isSpeaking reads the meter map by participant key', () => {
  const s = stage({ speakingIds: { 'u:2': true, 'u:3': false } });
  assert.equal(s.isSpeaking('u:2'), true);
  assert.equal(s.isSpeaking('u:3'), false);
  assert.equal(s.isSpeaking('u:9'), false);
});

test('gridColumns grows as the square root of the remote tiles', () => {
  const remotes = (n) => Array.from({ length: n }, (_, i) => ({ participant_key: `u:${i + 2}` }));
  assert.equal(stage({ callParticipants: [] }).gridColumns(), 1);
  assert.equal(stage({ callParticipants: remotes(1) }).gridColumns(), 1);
  assert.equal(stage({ callParticipants: remotes(4) }).gridColumns(), 2);
  assert.equal(stage({ callParticipants: remotes(5) }).gridColumns(), 3);
});

test('backToGrid drops the pin and keeps the choice manual', () => {
  const s = stage({ pinnedKey: 'u:2', pinnedManually: false });
  s.backToGrid();
  assert.equal(s.pinnedKey, null);
  assert.equal(s.pinnedManually, true, 'so auto-pin does not immediately undo it');
});

test('isSpotlight and spotlightParticipant follow the pinned row', () => {
  const ps = [{ participant_key: 'u:1' }, { participant_key: 'u:2', display_name: 'Bo' }];
  const grid = stage({ callParticipants: ps });
  assert.equal(grid.isSpotlight(), false);
  assert.equal(grid.spotlightParticipant(), null);

  const pinned = stage({ callParticipants: ps, pinnedKey: 'u:2', pinnedManually: true });
  assert.equal(pinned.isSpotlight(), true);
  assert.equal(pinned.spotlightParticipant().display_name, 'Bo');
});

test('hasVideo reads my own capture flags and everyone else s media state', () => {
  const me = { participant_key: 'u:1' };
  assert.equal(stage().hasVideo(me), false);
  assert.equal(stage({ cameraOn: true }).hasVideo(me), true);
  assert.equal(stage({ sharing: true }).hasVideo(me), true);

  const s = stage();
  assert.equal(s.hasVideo({ participant_key: 'u:2', media_state: { video: true } }), true);
  assert.equal(s.hasVideo({ participant_key: 'u:2', media_state: { screen: true } }), true);
  assert.equal(s.hasVideo({ participant_key: 'u:2', media_state: { audio: true } }), false);
  assert.equal(s.hasVideo({ participant_key: 'u:2' }), false);
  assert.equal(s.hasVideo(null), false);
});

test('streamFor hands out the local stream for me and the peer stream for others', () => {
  const local = { id: 'local' };
  const remote = { id: 'remote' };
  const s = stage({ localVideoStream: local, remoteStreams: { 'u:2': remote } });
  assert.equal(s.streamFor('u:1'), local);
  assert.equal(s.streamFor('u:2'), remote);
  assert.equal(s.streamFor('u:9'), null);
});

test('a departing peer that held the pin releases it', () => {
  const closed = [];
  const s = stage({
    callParticipants: [{ participant_key: 'u:1' }, { participant_key: 'u:2' }],
    pinnedKey: 'u:2',
    pinnedManually: true,
    _closePeer: (key) => closed.push(key),
  });

  s.onCallParticipantLeft({ participant_key: 'u:2' });

  assert.deepStrictEqual(Array.from(s.callParticipants, (p) => p.participant_key), ['u:1']);
  assert.deepStrictEqual(Array.from(closed), ['u:2']);
  assert.equal(s.pinnedKey, null);
  assert.equal(s.pinnedManually, false, 'auto-pin is allowed again');
});

test('a participant_left for another session is ignored while in a call', () => {
  const s = stage({
    inCall: true,
    callSession: { session_id: 's1' },
    callParticipants: [{ participant_key: 'u:2' }],
  });

  s.onCallParticipantLeft({ participant_key: 'u:2', session_id: 'another' });

  assert.deepStrictEqual(Array.from(s.callParticipants, (p) => p.participant_key), ['u:2']);
});

test('the duration timer starts from the server clock, is idempotent, and stops', () => {
  const timers = { armed: 0, cleared: [] };
  const s = stage(
    { callSession: { started_at: new Date(Date.now() - 65000).toISOString() } },
    {
      setInterval: () => { timers.armed += 1; return timers.armed; },
      clearInterval: (id) => timers.cleared.push(id),
    },
  );

  s._startDurationTimer();
  assert.equal(s.callElapsed, '01:05', 'counted from the call, not from my join');
  s._startDurationTimer();
  assert.equal(timers.armed, 1, 'arming twice keeps one interval');

  s._stopDurationTimer();
  assert.deepStrictEqual(Array.from(timers.cleared), [1]);
  assert.equal(s._durationTimer, null);
  s._stopDurationTimer();
  assert.equal(timers.cleared.length, 1, 'stopping twice clears nothing extra');
});

test('with no server clock the timer counts from now', () => {
  const s = stage({}, { setInterval: () => 1, clearInterval: () => {} });
  s._startDurationTimer();
  assert.equal(s.callElapsed, '00:00');
});

test('capacityLabel names the cap when the call has one', () => {
  const two = [{ participant_key: 'u:1' }, { participant_key: 'u:2' }];
  assert.equal(stage({ callParticipants: two, callSession: { max_participants: 6 } }).capacityLabel(), '2 / 6');
  assert.equal(stage({ callParticipants: two }).capacityLabel(), '2');
  assert.equal(stage({ callParticipants: null }).capacityLabel(), '0');
});
