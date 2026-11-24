'use strict';

// A conversation someone starts while the page is open is not in the list the
// page was served with. Its first message lists it, so the row the sidebar
// refresh adds shows the unread dot.

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

function buildApp(fetched) {
  const requests = [];
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/sse.js', {
    clearTimeout: () => {},
    document: { getElementById: () => null },
    chatThreadRouteTargets: () => ({ mainFlow: true, bumpRoot: null, panel: false }),
    fetch: async (url) => {
      requests.push(url);
      return { ok: true, json: async () => fetched };
    },
  });
  const app = ctx.chatSseMixin();
  Object.assign(app, {
    conversations: [{ uuid: 'known', unread_count: 0, updated_at: '2026-01-01T00:00:00Z' }],
    activeConversation: { uuid: 'known' },
    openThreadRoot: null,
    chatPrefs: {},
    isBotMessage: () => false,
    refreshConversationItems: () => {},
  });
  return { app, requests };
}

const message = (conversation) => ({
  conversation_id: conversation,
  message: { uuid: 'm-1', author: { id: 2 }, body: 'hi', created_at: '2026-02-01T00:00:00Z' },
});

test('the first message of a new conversation lists it as unread', async () => {
  const { app, requests } = buildApp({ uuid: 'new', unread_count: 0 });

  await app.handleSSEMessage(message('new'));

  assert.deepStrictEqual(requests, ['/api/v1/chat/conversations/new']);
  const listed = app.conversations.find((c) => c.uuid === 'new');
  assert.ok(listed && listed.unread_count > 0);
});

test('a conversation already listed is not fetched again', async () => {
  const { app, requests } = buildApp(null);
  app.activeConversation = null;

  await app.handleSSEMessage(message('known'));

  assert.deepStrictEqual(requests, []);
  assert.equal(app.conversations[0].unread_count, 1);
});
