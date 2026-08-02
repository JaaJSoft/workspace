'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

function buildSseApp() {
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/sse.js', {
    clearTimeout: () => {},
  });
  const calls = { refresh: [] };
  const app = ctx.chatSseMixin();
  Object.assign(app, {
    conversations: [
      { uuid: 'conv-1', title: 'Old title' },
      { uuid: 'conv-2', title: 'Other' },
    ],
    activeConversation: { uuid: 'conv-1', title: 'Old title' },
    titleRegeneratingUuid: null,
    refreshConversationItems(uuids, options) { calls.refresh.push({ uuids, options }); },
  });
  return { app, calls };
}

test('conversation_updated syncs the list entry and the active conversation', () => {
  const { app, calls } = buildSseApp();

  app.handleSSEConversationUpdated({ conversation_id: 'conv-1', title: 'New title' });

  assert.equal(app.conversations[0].title, 'New title');
  assert.equal(app.activeConversation.title, 'New title');
  assert.equal(calls.refresh.length, 1);
  assert.deepStrictEqual(Array.from(calls.refresh[0].uuids), ['conv-1']);
  assert.equal(calls.refresh[0].options.bump, false, 'a title change must not bump the row');
});

test('conversation_updated leaves the active conversation alone when another one changed', () => {
  const { app } = buildSseApp();

  app.handleSSEConversationUpdated({ conversation_id: 'conv-2', title: 'Renamed' });

  assert.equal(app.conversations[1].title, 'Renamed');
  assert.equal(app.activeConversation.title, 'Old title');
});

test('conversation_updated clears the regenerate loader for the matching conversation', () => {
  const { app } = buildSseApp();
  app.titleRegeneratingUuid = 'conv-1';

  app.handleSSEConversationUpdated({ conversation_id: 'conv-1', title: 'New title' });
  assert.equal(app.titleRegeneratingUuid, null);

  app.titleRegeneratingUuid = 'conv-2';
  app.handleSSEConversationUpdated({ conversation_id: 'conv-1', title: 'Again' });
  assert.equal(app.titleRegeneratingUuid, 'conv-2', 'a different conversation keeps its loader');
});

function buildConversationsApp({ fetchOk = true } = {}) {
  const timers = { set: 0, cleared: 0 };
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/conversations.js', {
    document: { getElementById: () => null },
    getCSRFToken: () => 'token',
    setTimeout: (fn) => { timers.set++; return timers.set; },
    clearTimeout: () => { timers.cleared++; },
    fetch: async () => ({ ok: fetchOk, status: fetchOk ? 202 : 500 }),
  });
  return { app: ctx.chatConversationsMixin(), timers };
}

test('regenerateConversationTitle raises the loader flag while the task runs', async () => {
  const { app } = buildConversationsApp();

  await app.regenerateConversationTitle('conv-1');

  assert.equal(app.titleRegeneratingUuid, 'conv-1', 'loader stays up until the SSE event lands');
});

test('regenerateConversationTitle clears the loader when the request fails', async () => {
  const { app } = buildConversationsApp({ fetchOk: false });

  await app.regenerateConversationTitle('conv-1');

  assert.equal(app.titleRegeneratingUuid, null);
});
