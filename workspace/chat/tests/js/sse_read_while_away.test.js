'use strict';

// A chat tab left open on one device must not mark incoming messages read:
// that clears their notification, and the push task skips read notifications,
// so the user's phone would never ring.

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

// Whether the page is attended is page_attention.js's call, tested in core.
function buildApp({ attended = true } = {}) {
  const page = { attended };
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/sse.js', {
    clearTimeout: () => {},
    document: { getElementById: () => null },
    pageAttention: { isAttended: () => page.attended },
    chatThreadRouteTargets: () => ({ mainFlow: true, bumpRoot: null, panel: false }),
  });
  const calls = { markAsRead: [], refresh: [] };
  const app = ctx.chatSseMixin();
  Object.assign(app, {
    conversations: [{ uuid: 'conv-1', unread_count: 0 }],
    activeConversation: { uuid: 'conv-1' },
    openThreadRoot: null,
    chatPrefs: {},
    botTyping: false,
    isBotMessage: () => false,
    _messageIdPrefix: () => 'msg',
    _isNearBottom: () => false,
    _refreshCurrentMessages: async () => {},
    _animateMessageEntry: () => {},
    _updateConversationLastMessage: () => {},
    scrollToBottom: () => {},
    markAsRead: async (id) => { calls.markAsRead.push(id); },
    refreshConversationItems: (uuids, options) => { calls.refresh.push({ uuids: Array.from(uuids), options }); },
  });
  return { app, calls, page };
}

const incoming = { conversation_id: 'conv-1', message: { uuid: 'm-1' } };

test('a message in the open conversation is marked read while the page is watched', async () => {
  const { app, calls } = buildApp();

  await app.handleSSEMessage(incoming);

  assert.deepStrictEqual(calls.markAsRead, ['conv-1']);
  assert.equal(app.unreadWhileAway, null);
});

test('an unattended page leaves the message unread', async () => {
  const { app, calls } = buildApp({ attended: false });

  await app.handleSSEMessage(incoming);

  assert.deepStrictEqual(calls.markAsRead, []);
  assert.equal(app.unreadWhileAway, 'conv-1');
});

test('coming back to the page marks the waiting conversation read', async () => {
  const { app, calls, page } = buildApp({ attended: false });
  await app.handleSSEMessage(incoming);
  app.conversations[0].unread_count = 1;

  await app.catchUpUnreadOnReturn();
  assert.deepStrictEqual(calls.markAsRead, [], 'still away: nothing to catch up yet');

  page.attended = true;
  await app.catchUpUnreadOnReturn();

  assert.deepStrictEqual(calls.markAsRead, ['conv-1']);
  assert.equal(app.conversations[0].unread_count, 0);
  assert.equal(app.unreadWhileAway, null);
  const last = calls.refresh[calls.refresh.length - 1];
  assert.deepStrictEqual(last.uuids, ['conv-1']);
  assert.equal(last.options.bump, false);
});

test('coming back after switching conversation does not mark the old one read', async () => {
  const { app, calls, page } = buildApp({ attended: false });
  await app.handleSSEMessage(incoming);
  app.activeConversation = { uuid: 'conv-2' };

  page.attended = true;
  await app.catchUpUnreadOnReturn();

  assert.deepStrictEqual(calls.markAsRead, []);
  assert.equal(app.unreadWhileAway, null);
});
