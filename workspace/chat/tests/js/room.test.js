'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// Stub the mixins and helpers the factory spreads, injected via extraGlobals
// so they are resolvable when chatRoomApp() is called inside the vm context.
const stubs = {
  chatMessagesMixin: () => ({ _msg: true, loadMessages: async () => {} }),
  chatInputMixin: () => ({ _input: true }),
  chatSseMixin: () => ({ _sse: true }),
  chatMembersMixin: () => ({ _members: true }),
  chatPanelsMixin: () => ({ _panels: true }),
  chatBotMixin: () => ({ _bot: true }),
  chatCallMixin: () => ({ startOrJoinCall: async () => {}, _start: true }),
  chatCallShouldOwnMedia: (r) => r !== 'observer',
};

const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/room.js', stubs);

test('chatRoomApp exposes factory on window', () => {
  assert.equal(typeof ctx.chatRoomApp, 'function');
});

test('chatRoomApp composes call mixin and owns media', () => {
  const app = ctx.chatRoomApp(1, 'conv-1');
  assert.equal(app.callRole, 'owner');
  assert.equal(app.roomConversationId, 'conv-1');
  assert.equal(typeof app.startOrJoinCall, 'function');
});

test('chatRoomApp sets currentUserId and initialises roomParticipants', () => {
  const app = ctx.chatRoomApp(42, 'conv-2');
  assert.equal(app.currentUserId, 42);
  assert.ok(Array.isArray(app.roomParticipants), 'roomParticipants must be an array');
});
