'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

const A = '0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b';

// The conversations mixin with every collaborator of selectConversation
// stubbed. The opening's requests have all answered by the time its last
// step runs, so what the reader did meanwhile is the `nearBottom` it finds.
function buildApp({ nearBottom }) {
  const calls = [];
  const ctx = loadScripts(['workspace/common/static/ui/js/uuid.js', 'workspace/chat/ui/static/chat/ui/js/conversations.js'], {
    document: { getElementById: () => null },
    localStorage: { getItem: () => '', setItem() {}, removeItem() {} },
    location: { pathname: '/chat' },
    getCSRFToken: () => 'token',
    navHistory: { push() {}, replace() {} },
    fetch: async () => ({ ok: true, json: async () => ({}) }),
  });
  const app = ctx.chatConversationsMixin();
  Object.assign(app, {
    refreshConversationList() {},
    messageBody: '',
    pendingFiles: [],
    $nextTick: async (fn) => {
      if (fn) fn();
    },
    async loadMessages() {},
    async markAsRead() {},
    async loadPinnedMessages() {},
    _isNearBottom: () => nearBottom,
    scrollToBottom: () => calls.push('scroll'),
    getMessageInput: () => ({ focus: () => calls.push('focus') }),
  });
  return { app, calls };
}

test('the opening ends on the newest message for a reader still at the end', async () => {
  const { app, calls } = buildApp({ nearBottom: true });

  await app.selectConversation({ uuid: A });

  assert.deepStrictEqual(calls, ['scroll', 'focus']);
});

test('the opening leaves a reader who went up where they are', async () => {
  // Its last step runs once the read receipt and the pins have answered: by
  // then the reader may have scrolled up or loaded an older page.
  const { app, calls } = buildApp({ nearBottom: false });

  await app.selectConversation({ uuid: A });

  assert.deepStrictEqual(calls, ['focus']);
});
