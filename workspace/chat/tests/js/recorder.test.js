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

test('recordedDurationSeconds measures the real elapsed time', () => {
  assert.equal(ctx.recordedDurationSeconds(1000, 4500, 300), 3.5);
});

test('recordedDurationSeconds keeps a sub-second clip sendable', () => {
  // The tick counter reports 0 for these; the endpoint rejects duration <= 0.
  assert.equal(ctx.recordedDurationSeconds(1000, 1400, 300), 0.4);
});

test('recordedDurationSeconds never returns zero', () => {
  assert.ok(ctx.recordedDurationSeconds(1000, 1000, 300) > 0);
});

test('recordedDurationSeconds survives a clock that went backwards', () => {
  assert.ok(ctx.recordedDurationSeconds(5000, 1000, 300) > 0);
});

test('recordedDurationSeconds clamps to the configured maximum', () => {
  assert.equal(ctx.recordedDurationSeconds(0, 900000, 300), 300);
});

test('_finalizeRecording ignores a stale stop event', () => {
  // A cancelled recorder's queued onstop must not release a stream that
  // belongs to a newer recording, nor resurrect the preview.
  const m = ctx.chatRecorderMixin();
  m.recorderState = 'idle';
  m._recorderStream = {
    getTracks() {
      throw new Error('released the wrong stream');
    },
  };
  m._recorderChunks = [{ size: 10 }];
  m._finalizeRecording();
  assert.equal(m.recorderState, 'idle');
  assert.equal(m.recordedFile, null);
});

test('stopRecording tolerates a double click', () => {
  const m = ctx.chatRecorderMixin();
  m.recorderState = 'recording';
  let stops = 0;
  m._recorder = {
    state: 'recording',
    stop() {
      stops += 1;
      this.state = 'inactive';
    },
  };
  m.stopRecording();
  m.stopRecording();
  assert.equal(stops, 1);
});

test('sendRecording keeps the recording when the send fails', async () => {
  const m = ctx.chatRecorderMixin();
  m.recorderState = 'preview';
  m.recordedFile = { name: 'voice.webm' };
  m.recordedUrl = 'blob:kept';
  m.recordedDuration = 2.5;
  m.sendVoiceMessage = async () => false;

  await m.sendRecording();

  assert.equal(m.recorderState, 'preview');
  assert.equal(m.recordedUrl, 'blob:kept');
  assert.equal(m.recordedFile.name, 'voice.webm');
  assert.equal(m.sendingRecording, false);
});

test('sendRecording discards the recording once the send succeeded', async () => {
  const revoked = [];
  ctx.URL = { revokeObjectURL: (u) => revoked.push(u) };
  const m = ctx.chatRecorderMixin();
  m.recorderState = 'preview';
  m.recordedFile = { name: 'voice.webm' };
  m.recordedUrl = 'blob:sent';
  m.recordedDuration = 2.5;
  m.sendVoiceMessage = async () => true;

  await m.sendRecording();

  assert.equal(m.recorderState, 'idle');
  assert.equal(m.recordedUrl, null);
  assert.equal(m.recordedFile, null);
  assert.deepStrictEqual(Array.from(revoked), ['blob:sent']);
});

test('sendRecording sends the measured duration once per click', async () => {
  ctx.URL = { revokeObjectURL: () => {} };
  const m = ctx.chatRecorderMixin();
  m.recorderState = 'preview';
  m.recordedFile = { name: 'voice.webm' };
  m.recordedUrl = 'blob:once';
  m.recordedDuration = 0.4;
  const sent = [];
  m.sendVoiceMessage = async (file, duration) => {
    sent.push(duration);
    return true;
  };

  const first = m.sendRecording();
  const second = m.sendRecording();
  await Promise.all([first, second]);

  assert.deepStrictEqual(Array.from(sent), [0.4]);
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
