'use strict';

// One SSE poll can carry many reactions, pins, deletions and read receipts
// for the open conversation. Each used to refetch the whole message window.

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

function buildApp({ pendingReceipt = false } = {}) {
  const timers = [];
  const page = { pendingReceipt };
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/sse.js', {
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    clearTimeout: () => {},
    document: {
      getElementById: () => null,
      querySelector: (selector) => (selector === '[data-read-pending]' && page.pendingReceipt ? {} : null),
    },
  });
  const calls = { messages: 0, pins: [] };
  const app = ctx.chatSseMixin();
  Object.assign(app, {
    activeConversation: { uuid: 'conv-1' },
    _isNearBottom: () => false,
    _refreshCurrentMessages: async () => { calls.messages++; },
    loadPinnedMessages: (uuid) => { calls.pins.push(uuid); },
  });
  const flush = async () => {
    while (timers.length) await timers.shift()();
  };
  return { app, calls, flush, page };
}

const inConv = (extra = {}) => ({ conversation_id: 'conv-1', message_id: 'm-1', ...extra });

test('a burst of reactions, pins, previews and deletions refetches the messages once', async () => {
  const { app, calls, flush } = buildApp();

  for (let i = 0; i < 5; i++) app.handleSSEReaction(inConv({ emoji: '👍' }));
  app.handleSSEMessagePinned(inConv());
  app.handleSSELinkPreview(inConv());
  app.handleSSEMessageDeleted(inConv());
  app.handleSSEMessageDeleted(inConv({ message_id: 'm-2' }));
  await flush();

  assert.equal(calls.messages, 1);
  assert.deepStrictEqual(calls.pins, ['conv-1']);
});

test('events of another conversation refetch nothing', async () => {
  const { app, calls, flush } = buildApp({ pendingReceipt: true });

  app.handleSSEReaction({ conversation_id: 'conv-2', message_id: 'm-1' });
  app.handleSSERead({ conversation_id: 'conv-2' });
  await flush();

  assert.equal(calls.messages, 0);
});

test('a read receipt refetches nothing when no receipt on screen is waiting on readers', async () => {
  const { app, calls, flush } = buildApp({ pendingReceipt: false });

  app.handleSSERead({ conversation_id: 'conv-1' });
  await flush();

  assert.equal(calls.messages, 0);
});

test('a read arriving during a reload is judged on what that reload brings, by a request of its own', async () => {
  const { app, calls, flush, page } = buildApp();
  let landed;
  app._messagesRefreshing = 1;
  app._messagesIdle = () => new Promise((resolve) => { landed = resolve; });

  const reading = app.handleSSERead(inConv());
  await flush();
  assert.equal(calls.messages, 0);

  // The reload lands with a receipt the read concerns, then a fresh request goes out.
  page.pendingReceipt = true;
  app._messagesRefreshing = 0;
  landed();
  await reading;
  await flush();

  assert.equal(calls.messages, 1);
});

test('a read arriving during a reload that brings no waiting receipt refetches nothing', async () => {
  const { app, calls, flush } = buildApp();
  app._messagesRefreshing = 1;
  app._messagesIdle = () => Promise.resolve();

  await app.handleSSERead(inConv());
  await flush();

  assert.equal(calls.messages, 0);
});

test('a read receipt refreshes a receipt still waiting on readers', async () => {
  const { app, calls, flush } = buildApp({ pendingReceipt: true });

  app.handleSSERead({ conversation_id: 'conv-1' });
  app.handleSSERead({ conversation_id: 'conv-1' });
  await flush();

  assert.equal(calls.messages, 1);
});

test('a refresh scheduled for a conversation the user has since left is dropped', async () => {
  const { app, calls, flush } = buildApp();

  app.handleSSEReaction(inConv());
  app.activeConversation = { uuid: 'conv-2' };
  await flush();

  assert.equal(calls.messages, 0);
});

test('an unpin reloads the pinned bar along with the messages', async () => {
  const { app, calls, flush } = buildApp();

  app.handleSSEMessageUnpinned(inConv());
  await flush();

  assert.equal(calls.messages, 1);
  assert.deepStrictEqual(calls.pins, ['conv-1']);
});
