'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/recorder.js');

test('pickVoiceRecordingType prefers opus when available', () => {
  const picked = ctx.pickVoiceRecordingType(() => true);
  assert.equal(picked.mime, 'audio/webm;codecs=opus');
  assert.equal(picked.ext, 'webm');
});

test('pickVoiceRecordingType falls back to plain webm', () => {
  const picked = ctx.pickVoiceRecordingType((m) => m === 'audio/webm');
  assert.equal(picked.mime, 'audio/webm');
  assert.equal(picked.ext, 'webm');
});

test('pickVoiceRecordingType falls back to mp4 on Safari', () => {
  const picked = ctx.pickVoiceRecordingType((m) => m === 'audio/mp4');
  assert.equal(picked.mime, 'audio/mp4');
  assert.equal(picked.ext, 'm4a');
});

test('pickVoiceRecordingType returns null when nothing is supported', () => {
  assert.equal(ctx.pickVoiceRecordingType(() => false), null);
});

test('pickVoiceRecordingType tolerates a missing isTypeSupported', () => {
  // Browsers without MediaRecorder must disable the button, not throw.
  assert.equal(ctx.pickVoiceRecordingType(undefined), null);
});

test('every declared voice type carries a matching extension', () => {
  for (const t of Array.from(ctx.CHAT_VOICE_TYPES)) {
    assert.ok(t.mime.startsWith('audio/'), t.mime);
    assert.ok(/^[a-z0-9]+$/.test(t.ext), t.ext);
  }
});

test('voiceFileName flattens the timestamp into a safe filename', () => {
  assert.equal(
    ctx.voiceFileName('webm', '2026-08-05T14:32:07.145Z'),
    'voice-2026-08-05T14-32-07-145Z.webm'
  );
});

test('voiceFileName keeps colons and dots out of the name', () => {
  // Colons are illegal in Windows filenames and awkward in Content-Disposition.
  const name = ctx.voiceFileName('m4a', '2026-08-05T14:32:07.145Z');
  assert.ok(!name.slice(0, -4).includes(':'));
  assert.ok(!name.slice(0, -4).includes('.'));
  assert.ok(name.endsWith('.m4a'));
});

test('chatRecorderMixin starts idle', () => {
  const m = ctx.chatRecorderMixin();
  assert.equal(m.recorderState, 'idle');
  assert.equal(m.recordSeconds, 0);
  assert.equal(m.recordedFile, null);
});

test('chatRecorderMixin re-entry guard starts clear', () => {
  const m = ctx.chatRecorderMixin();
  assert.equal(m._startingRecording, false);
});

test('chatRecorderMixin exposes no getter', () => {
  // A `get` accessor would be flattened to a frozen value by the spread that
  // builds chatApp(), and would never recompute.
  const m = ctx.chatRecorderMixin();
  for (const key of Object.keys(m)) {
    const d = Object.getOwnPropertyDescriptor(m, key);
    assert.equal(typeof d.get, 'undefined', 'getter found on ' + key);
  }
});
