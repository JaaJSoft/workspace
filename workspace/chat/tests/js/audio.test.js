'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/audio.js');

test('formatAudioTime renders mm:ss', () => {
  assert.equal(ctx.formatAudioTime(0), '0:00');
  assert.equal(ctx.formatAudioTime(7), '0:07');
  assert.equal(ctx.formatAudioTime(65), '1:05');
  assert.equal(ctx.formatAudioTime(600), '10:00');
  assert.equal(ctx.formatAudioTime(3671), '61:11');
});

test('formatAudioTime truncates rather than rounds', () => {
  // 7.9s must read 0:07 while the bar sits at 79% of the 8th second, not
  // jump to 0:08 before the audio gets there.
  assert.equal(ctx.formatAudioTime(7.9), '0:07');
});

test('formatAudioTime degrades gracefully on an unknown duration', () => {
  // MediaRecorder WebM reports Infinity until the file is seeked to its end.
  assert.equal(ctx.formatAudioTime(Infinity), '--:--');
  assert.equal(ctx.formatAudioTime(NaN), '--:--');
  assert.equal(ctx.formatAudioTime(-3), '--:--');
  assert.equal(ctx.formatAudioTime(undefined), '--:--');
});

test('audioProgressPercent maps elapsed onto 0-100', () => {
  assert.equal(ctx.audioProgressPercent(0, 20), 0);
  assert.equal(ctx.audioProgressPercent(5, 20), 25);
  assert.equal(ctx.audioProgressPercent(20, 20), 100);
});

test('audioProgressPercent never leaves the 0-100 range', () => {
  assert.equal(ctx.audioProgressPercent(30, 20), 100);
  assert.equal(ctx.audioProgressPercent(-5, 20), 0);
});

test('audioProgressPercent yields 0 when the duration is unusable', () => {
  assert.equal(ctx.audioProgressPercent(5, 0), 0);
  assert.equal(ctx.audioProgressPercent(5, Infinity), 0);
  assert.equal(ctx.audioProgressPercent(5, NaN), 0);
});

test('nextAudioSpeed cycles and wraps', () => {
  assert.equal(ctx.nextAudioSpeed(1), 1.5);
  assert.equal(ctx.nextAudioSpeed(1.5), 2);
  assert.equal(ctx.nextAudioSpeed(2), 1);
});

test('nextAudioSpeed recovers from an off-cycle value', () => {
  assert.equal(ctx.nextAudioSpeed(3), 1);
});

test('chatAudioPlayer seeds its duration from the server value', () => {
  assert.equal(ctx.chatAudioPlayer('u1', 12.5).duration, 12.5);
});

test('chatAudioPlayer tolerates a missing server duration', () => {
  assert.equal(ctx.chatAudioPlayer('u1', null).duration, 0);
  assert.equal(ctx.chatAudioPlayer('u1', undefined).duration, 0);
  assert.equal(ctx.chatAudioPlayer('u1', 0).duration, 0);
});

function playerWithAudio(audioStub) {
  ctx.CustomEvent = function CustomEvent(name, opts) {
    return { type: name, detail: opts && opts.detail };
  };
  ctx.dispatchEvent = () => {};
  const p = ctx.chatAudioPlayer('u1', 10);
  p.$refs = { audio: audioStub };
  return p;
}

test('play releases the button when playback is rejected', async () => {
  // A missing or undecodable source must not leave the toggle stuck on Pause.
  const p = playerWithAudio({
    playbackRate: 1,
    play: async () => {
      throw new Error('no supported source');
    },
  });

  await p.play();

  assert.equal(p.playing, false);
});

test('play marks the player as playing when it starts', async () => {
  const p = playerWithAudio({ playbackRate: 1, play: async () => {} });

  await p.play();

  assert.equal(p.playing, true);
});

test('chatAudioPlayer starts idle at x1 with details collapsed', () => {
  const p = ctx.chatAudioPlayer('u1', 10);
  assert.equal(p.playing, false);
  assert.equal(p.speed, 1);
  assert.equal(p.detailsOpen, false);
  assert.equal(p.currentTime, 0);
});
