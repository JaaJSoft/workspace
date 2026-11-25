'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// Stub the mixins and helpers the factory spreads, injected via extraGlobals
// so they are resolvable when chatRoomApp() is called inside the vm context.
const stubs = {
  chatUiHelpersMixin: () => ({}),
  chatConversationsMixin: () => ({ _conversations: true }),
  chatMessagesMixin: () => ({ _msg: true, loadMessages: async () => {} }),
  chatInputMixin: () => ({ _input: true }),
  chatSseMixin: () => ({ _sse: true }),
  chatMembersMixin: () => ({ _members: true }),
  chatPanelsMixin: () => ({ _panels: true }),
  chatThreadsMixin: () => ({ _threads: true }),
  chatBotMixin: () => ({ _bot: true }),
  chatCallMixin: () => ({ startOrJoinCall: async () => {}, _start: true }),
  chatCallDiagnosticMixin: () => ({ _diag: true }),
  chatRecorderMixin: () => ({ initRecorder: () => {} }),
};

const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/room.js', stubs);

test('chatRoomApp exposes factory on window', () => {
  assert.equal(typeof ctx.chatRoomApp, 'function');
});

test('chatRoomApp composes the call mixin', () => {
  const app = ctx.chatRoomApp(1, 'conv-1');
  assert.equal(app.roomConversationId, 'conv-1');
  assert.equal(typeof app.startOrJoinCall, 'function');
});

test('chatRoomApp sets currentUserId and roomConversationId', () => {
  const app = ctx.chatRoomApp(42, 'conv-2');
  assert.equal(app.currentUserId, 42);
  assert.equal(app.roomConversationId, 'conv-2');
});

test('the call room wires the conversation pane as the chat page does', async () => {
  let wired = 0;
  const roomCtx = loadScript('workspace/chat/ui/static/chat/ui/js/room.js', {
    ...stubs,
    chatInputMixin: () => ({ listenToConversationPane() { wired += 1; } }),
    document: { getElementById: () => null },
    addEventListener: () => {},
  });
  const app = roomCtx.chatRoomApp(1, 'conv-1');
  await app.init();
  assert.equal(wired, 1);
});
