const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/call_sounds.js');

test('fixed events map to their own cue', () => {
  assert.equal(ctx.chatCallSoundCue('join'), 'join');
  assert.equal(ctx.chatCallSoundCue('leave'), 'leave');
  assert.equal(ctx.chatCallSoundCue('peer-join'), 'peer-join');
  assert.equal(ctx.chatCallSoundCue('peer-leave'), 'peer-leave');
});

test('toggle-mute maps by the resulting muted state', () => {
  assert.equal(ctx.chatCallSoundCue('toggle-mute', true), 'mute');
  assert.equal(ctx.chatCallSoundCue('toggle-mute', false), 'unmute');
});

test('unknown event yields null', () => {
  assert.equal(ctx.chatCallSoundCue('nope'), null);
  assert.equal(ctx.chatCallSoundCue(undefined), null);
});

test('chatCallSounds exposes play and setEnabled', () => {
  assert.equal(typeof ctx.chatCallSounds.play, 'function');
  assert.equal(typeof ctx.chatCallSounds.setEnabled, 'function');
  // play must not throw in a non-browser context (no AudioContext).
  ctx.chatCallSounds.setEnabled(true);
  ctx.chatCallSounds.play('join');
  ctx.chatCallSounds.play(null);
});

test('play is a no-op (no throw) when disabled', () => {
  // The disabled path returns before touching Web Audio at all.
  ctx.chatCallSounds.setEnabled(false);
  ctx.chatCallSounds.play('join');
  ctx.chatCallSounds.setEnabled(true); // restore for any later assertions
});
