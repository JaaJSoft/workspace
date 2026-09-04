'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript, loadScripts } = require('../../../common/tests/js/loader');

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
  chatMeetingHostMixin: () => ({ loadLobby: async () => {} }),
  chatCallDiagnosticMixin: () => ({ _diag: true }),
  chatRecorderMixin: () => ({ initRecorder: () => {} }),
  chatCallShouldOwnMedia: (r) => r !== 'observer',
};

const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/room.js', stubs);

// Integration test against the REAL chatCallMixin (not the double above): the
// stubbed suite cannot catch currentParticipantKey being clobbered by a mixin
// spread, since the double never declares that key. loadScripts runs the real
// call.js, call_room.js and room.js in one shared context, mirroring the load
// order base.html uses in the browser.
const nonCallStubs = {
  chatUiHelpersMixin: () => ({}),
  chatConversationsMixin: () => ({ _conversations: true }),
  chatMessagesMixin: () => ({ _msg: true, loadMessages: async () => {} }),
  chatInputMixin: () => ({ _input: true }),
  chatSseMixin: () => ({ _sse: true }),
  chatMembersMixin: () => ({ _members: true }),
  chatPanelsMixin: () => ({ _panels: true }),
  chatThreadsMixin: () => ({ _threads: true }),
  chatBotMixin: () => ({ _bot: true }),
  chatMeetingHostMixin: () => ({ loadLobby: async () => {} }),
  chatCallDiagnosticMixin: () => ({ _diag: true }),
  chatRecorderMixin: () => ({ initRecorder: () => {} }),
};

const integrationCtx = loadScripts(
  [
    'workspace/chat/ui/static/chat/ui/js/call.js',
    'workspace/chat/ui/static/chat/ui/js/call_room.js',
    'workspace/chat/ui/static/chat/ui/js/room.js',
  ],
  nonCallStubs,
);

test('chatRoomApp composed with the real chatCallMixin keeps its derived participant key', () => {
  const app = integrationCtx.chatRoomApp(7, 'conv-1');
  assert.equal(app.currentParticipantKey, 'u:7');
});

test('chatRoomApp exposes factory on window', () => {
  assert.equal(typeof ctx.chatRoomApp, 'function');
});

test('chatRoomApp composes call mixin and owns media', () => {
  const app = ctx.chatRoomApp(1, 'conv-1');
  assert.equal(app.callRole, 'owner');
  assert.equal(app.roomConversationId, 'conv-1');
  assert.equal(typeof app.startOrJoinCall, 'function');
});

test('chatRoomApp sets currentUserId and roomConversationId', () => {
  const app = ctx.chatRoomApp(42, 'conv-2');
  assert.equal(app.currentUserId, 42);
  assert.equal(app.roomConversationId, 'conv-2');
  assert.equal(app.callRole, 'owner');
});

test('chatRoomApp derives its own participant key from the user id', () => {
  const app = ctx.chatRoomApp(7, 'conv-1');
  assert.equal(app.currentParticipantKey, 'u:7');
});

test('remoteParticipants excludes self by participant key', () => {
  const app = ctx.chatRoomApp(7, 'conv-1');
  app.callParticipants = [
    { participant_key: 'u:7', user_id: 7, display_name: 'me' },
    { participant_key: 'u:8', user_id: 8, display_name: 'you' },
    { participant_key: 'g:abc', user_id: null, display_name: 'guest' },
  ];
  const keys = app.remoteParticipants().map((p) => p.participant_key);
  assert.deepStrictEqual(Array.from(keys), ['u:8', 'g:abc']);
});

test('selfParticipant finds the row matching the participant key', () => {
  const app = ctx.chatRoomApp(7, 'conv-1');
  app.callParticipants = [{ participant_key: 'u:7', user_id: 7, display_name: 'me' }];
  assert.equal(app.selfParticipant().display_name, 'me');
});

test('pinTile toggles on a participant key', () => {
  const app = ctx.chatRoomApp(7, 'conv-1');
  app.pinTile('g:abc');
  assert.equal(app.pinnedKey, 'g:abc');
  app.pinTile('g:abc');
  assert.equal(app.pinnedKey, null);
});

test('the room teardown stops the lobby refresh and still tears down the panel', () => {
  const stopped = [];
  const ctx2 = loadScript('workspace/chat/ui/static/chat/ui/js/room.js', {
    ...stubs,
    // Two mixins with a teardown: object spread would let the later one win
    // silently, which is the whole reason destroy() is declared on the room.
    chatThreadsMixin: () => ({ destroy() { stopped.push('threads'); } }),
    chatMeetingHostMixin: () => ({
      loadLobby: async () => {},
      _stopLobbyRefresh() { stopped.push('lobby'); },
    }),
  });
  const app = ctx2.chatRoomApp(7, 'conv-1');
  app.destroy();
  assert.deepStrictEqual(Array.from(stopped).sort(), ['lobby', 'threads']);
});
