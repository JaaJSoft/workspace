'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const A = '0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b';
const B = '0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5c';

// The conversations mixin with every collaborator of selectConversation
// stubbed, and navHistory recording each write.
function buildApp({ pathname = '/chat', returnToStepsBack = false, fetch = null } = {}) {
  const writes = [];
  const location = { pathname };
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/conversations.js', {
    document: { getElementById: () => null },
    localStorage: { getItem: () => '', setItem() {}, removeItem() {} },
    location,
    getCSRFToken: () => 'token',
    navHistory: {
      push: (url) => writes.push(['push', url]),
      replace: (url) => writes.push(['replace', url]),
      returnTo: (url) => {
        writes.push(['returnTo', url]);
        return returnToStepsBack;
      },
    },
    fetch:
      fetch || (async (url) => ({ ok: true, json: async () => ({ uuid: url.split('/').pop() }) })),
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
    scrollToBottom() {},
    getMessageInput: () => null,
  });
  return { app, writes, location };
}

test('opening a conversation from the list pushes an entry', async () => {
  const { app, writes } = buildApp();

  await app.selectConversation({ uuid: A });

  assert.deepStrictEqual(writes, [['push', `/chat/${A}`]]);
});

test('switching from an open conversation replaces the entry', async () => {
  const { app, writes } = buildApp();
  await app.selectConversation({ uuid: A });

  await app.selectConversation({ uuid: B });

  assert.deepStrictEqual(writes, [
    ['push', `/chat/${A}`],
    ['replace', `/chat/${B}`],
  ]);
});

test('closing returns to the list instead of stacking an entry', async () => {
  const { app, writes } = buildApp();
  await app.selectConversation({ uuid: A }, false);

  app.closeConversation();

  assert.equal(app.activeConversation, null);
  assert.deepStrictEqual(writes, [['returnTo', '/chat']]);
});

test('a restore onto the list closes the conversation without writing history', async () => {
  const { app, writes, location } = buildApp();
  await app.selectConversation({ uuid: A }, false);
  location.pathname = '/chat';

  await app.restoreConversationFromUrl();

  assert.equal(app.activeConversation, null);
  assert.deepStrictEqual(writes, []);
});

test('a restore onto a conversation selects it without writing history', async () => {
  const { app, writes, location } = buildApp();
  await app.selectConversation({ uuid: A }, false);
  location.pathname = `/chat/${B}`;

  await app.restoreConversationFromUrl();

  assert.equal(app.activeConversation.uuid, B);
  assert.deepStrictEqual(writes, []);
});

test('a restore onto the conversation already open leaves it alone', async () => {
  const { app, location } = buildApp();
  const conv = { uuid: A };
  await app.selectConversation(conv, false);
  location.pathname = `/chat/${A}`;

  await app.restoreConversationFromUrl();

  assert.equal(app.activeConversation, conv);
});

test('a ?dm= landing rewrites its entry rather than stacking one', async () => {
  const { app, writes } = buildApp({
    pathname: '/chat',
    fetch: async () => ({ ok: true, json: async () => ({ uuid: A }) }),
  });

  await app._openDmByUserId(42);

  assert.equal(app.activeConversation.uuid, A);
  assert.deepStrictEqual(writes, [['replace', `/chat/${A}`]]);
});
