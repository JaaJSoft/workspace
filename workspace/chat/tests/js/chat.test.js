'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

// chatApp() reads window.matchMedia and localStorage synchronously while
// building its object literal (the `collapsed` field), before init() ever
// runs, so both need a stub even for tests that never call init().
const matchMediaStub = () => ({ matches: false, addEventListener: () => {} });
const localStorageStub = { getItem: () => null, setItem: () => {} };

// Integration test against the REAL chatCallMixin (not a double): a stubbed
// mixin never declares currentParticipantKey, so it cannot catch the key
// being clobbered by a mixin spread. loadScripts runs the real call.js,
// call_room.js and chat.js in one shared context, mirroring the load order
// base.html uses in the browser.
const nonCallStubs = {
  matchMedia: matchMediaStub,
  localStorage: localStorageStub,
  chatUiHelpersMixin: () => ({}),
  chatConversationsMixin: () => ({ _conversations: true }),
  chatMessagesMixin: () => ({ _msg: true, loadMessages: async () => {} }),
  chatSseMixin: () => ({ _sse: true }),
  chatMembersMixin: () => ({ _members: true }),
  chatPanelsMixin: () => ({ _panels: true }),
  chatThreadsMixin: () => ({ _threads: true }),
  chatBotMixin: () => ({ _bot: true }),
  chatInputMixin: () => ({ _input: true }),
  chatCallDiagnosticMixin: () => ({ _diag: true }),
  chatRecorderMixin: () => ({ initRecorder: () => {} }),
};

const integrationCtx = loadScripts(
  [
    'workspace/chat/ui/static/chat/ui/js/call.js',
    'workspace/chat/ui/static/chat/ui/js/call_room.js',
    'workspace/chat/ui/static/chat/ui/js/chat.js',
  ],
  nonCallStubs,
);

test('chatApp composed with the real chatCallMixin derives its own participant key', () => {
  const app = integrationCtx.chatApp(7);
  assert.equal(app.currentParticipantKey, 'u:7');
});

test('callBannerAction returns return when the current user is already a call participant', () => {
  const app = integrationCtx.chatApp(7);
  app.callSession = { session_id: 'sess-1', conversation_id: 'conv-1' };
  app.callParticipants = [{ participant_key: 'u:7', user_id: 7, display_name: 'me' }];
  assert.equal(app.callBannerAction(), 'return');
});

test('callBannerAction returns join when the current user is not a call participant', () => {
  const app = integrationCtx.chatApp(7);
  app.callSession = { session_id: 'sess-1', conversation_id: 'conv-1' };
  app.callParticipants = [{ participant_key: 'u:8', user_id: 8, display_name: 'other' }];
  assert.equal(app.callBannerAction(), 'join');
});
