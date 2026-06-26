'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// chatRoomFormatDuration is a top-level function exposed on window - load the
// script with the same mixin stubs as room.test.js so it initialises cleanly,
// then call the formatter directly without instantiating the factory.
const stubs = {
  chatUiHelpersMixin: () => ({}),
  chatConversationsMixin: () => ({ _conversations: true }),
  chatMessagesMixin: () => ({ _msg: true, loadMessages: async () => {} }),
  chatInputMixin: () => ({ _input: true }),
  chatSseMixin: () => ({ _sse: true }),
  chatMembersMixin: () => ({ _members: true }),
  chatPanelsMixin: () => ({ _panels: true }),
  chatBotMixin: () => ({ _bot: true }),
  chatCallMixin: () => ({ startOrJoinCall: async () => {}, _start: true }),
  chatCallDiagnosticMixin: () => ({ _diag: true }),
  chatCallShouldOwnMedia: (r) => r !== 'observer',
};

const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/room.js', stubs);

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
