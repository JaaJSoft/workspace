'use strict';

// The sidebar list is refreshed from many places (a new message, a pin, a
// member change). Each refresh keeps the search the user typed, so a filtered
// list never turns back into the whole one under a query still on screen.

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

function buildApp() {
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/conversations.js', {
    // The chat page always renders the sidebar list.
    document: { getElementById: (id) => (id === 'conversation-list' ? {} : null) },
  });
  const app = ctx.chatConversationsMixin();
  const requests = [];
  app.$el = { addEventListener() {} };
  app.$ajax = (url, options) => { requests.push([url, options.target]); };
  return { app, requests };
}

test('a refresh during a search keeps the query', () => {
  const { app, requests } = buildApp();
  app.conversationSearch = ' ann & co ';

  app.refreshConversationList();

  assert.deepStrictEqual(requests.map((r) => Array.from(r)), [
    ['/chat/conversations?q=ann%20%26%20co', 'conversation-list'],
  ]);
});

test('without a search the whole list is asked for', () => {
  const { app, requests } = buildApp();

  app.refreshConversationList();

  assert.deepStrictEqual(requests.map((r) => r[0]), ['/chat/conversations']);
});
