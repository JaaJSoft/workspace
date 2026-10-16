'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const BOT_ID = 42;

function buildApp() {
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/bot.js', {
    document: { getElementById: () => null },
  });
  const app = { ...ctx.chatBotMixin() };
  app.availableBots = [{ user_id: BOT_ID }];
  return app;
}

test('a direct conversation with a bot is a bot conversation', () => {
  const app = buildApp();

  assert.equal(app.isBotConversation({ kind: 'dm', is_bot_conversation: true }), true);
  assert.equal(
    app.isBotConversation({ kind: 'dm', members: [{ user: { id: BOT_ID } }] }),
    true,
  );
});

test('a group is never a bot conversation, even with a bot among its members', () => {
  const app = buildApp();

  assert.equal(app.isBotConversation({ kind: 'group', is_bot_conversation: true }), false);
  assert.equal(
    app.isBotConversation({ kind: 'group', members: [{ user: { id: BOT_ID } }] }),
    false,
  );
});
