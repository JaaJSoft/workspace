'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// The formatter is a pure helper in call_room.js, which the member room, the
// main-tab observer and the guest page all load - no mixin stubs needed.
const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/call_room.js');

const fmt = ctx.chatRoomFormatDuration;

test('chatRoomFormatDuration: 0 ms -> 00:00', () => {
  assert.equal(fmt(0), '00:00');
});

test('chatRoomFormatDuration: 1000 ms -> 00:01', () => {
  assert.equal(fmt(1000), '00:01');
});

test('chatRoomFormatDuration: 65000 ms -> 01:05', () => {
  assert.equal(fmt(65000), '01:05');
});

test('chatRoomFormatDuration: 3600000 ms -> 1:00:00', () => {
  assert.equal(fmt(3600000), '1:00:00');
});

test('chatRoomFormatDuration: 3661000 ms -> 1:01:01', () => {
  assert.equal(fmt(3661000), '1:01:01');
});

test('chatRoomFormatDuration: negative -> 00:00', () => {
  assert.equal(fmt(-5000), '00:00');
});
