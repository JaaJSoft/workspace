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
