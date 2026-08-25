'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript, loadScripts } = require('../../../common/tests/js/loader');

// Composes the sse + bot mixins the way chatApp() does, with controllable
// timers so the failsafe hide can be triggered synchronously.
function buildApp() {
  const timers = { fns: [], cleared: 0 };
  const stubs = {
    setTimeout: (fn) => { timers.fns.push(fn); return timers.fns.length; },
    clearTimeout: (id) => { if (id) timers.cleared++; },
  };
  const sseCtx = loadScripts(
    [
      'workspace/chat/ui/static/chat/ui/js/threads.js',
      'workspace/chat/ui/static/chat/ui/js/sse.js',
    ],
    stubs,
  );
  const botCtx = loadScript('workspace/chat/ui/static/chat/ui/js/bot.js', stubs);
  // Same order as chatApp(): sse first, bot last, so an overlapping key
  // resolves here the way it does in production.
  const app = Object.assign({}, sseCtx.chatThreadsMixin(), sseCtx.chatSseMixin(), botCtx.chatBotMixin(), {
    activeConversation: { uuid: 'conv-1' },
  });
  return { app, timers };
}

test('bot_step raises the typing indicator and stores the step as running', () => {
  const { app } = buildApp();

  app.handleSSEBotStep({ conversation_id: 'conv-1', call_id: 'c1', html: '<span>Web Search</span>' });

  assert.equal(app.botTyping, true);
  assert.deepStrictEqual(
    Array.from(app.botSteps, s => ({ ...s })),
    [{ id: 'c1', html: '<span>Web Search</span>', running: true }],
  );
  assert.equal(app.botStepsRunning(), true);
});

test('a completion ends its own row, not the last one', () => {
  // A round dispatches its read-only tools together: the call that ends
  // first is rarely the one shown last.
  const { app } = buildApp();
  app.handleSSEBotStep({ conversation_id: 'conv-1', call_id: 'c1', html: '<span>Read A</span>' });
  app.handleSSEBotStep({ conversation_id: 'conv-1', call_id: 'c2', html: '<span>Read B</span>' });

  app.handleSSEBotStep({ conversation_id: 'conv-1', call_id: 'c1', done: true });

  assert.deepStrictEqual(
    Array.from(app.botSteps, s => [s.id, s.running]),
    [['c1', false], ['c2', true]],
  );
  assert.equal(app.botStepsRunning(), true);
});

test('the rows stay in call order whatever order the calls end in', () => {
  const { app } = buildApp();
  ['c1', 'c2', 'c3'].forEach(id =>
    app.handleSSEBotStep({ conversation_id: 'conv-1', call_id: id, html: `<span>${id}</span>` }),
  );

  ['c3', 'c1', 'c2'].forEach(id =>
    app.handleSSEBotStep({ conversation_id: 'conv-1', call_id: id, done: true }),
  );

  assert.deepStrictEqual(Array.from(app.botSteps, s => s.id), ['c1', 'c2', 'c3']);
  assert.equal(app.botStepsRunning(), false);
});

test('a completion whose opening step is unknown is ignored', () => {
  // Its step can have fallen out of the capped mailbox, or been queued
  // before this connection opened.
  const { app } = buildApp();
  app.handleSSEBotStep({ conversation_id: 'conv-1', call_id: 'c1', html: '<span>Read A</span>' });

  app.handleSSEBotStep({ conversation_id: 'conv-1', call_id: 'gone', done: true });

  assert.deepStrictEqual(
    Array.from(app.botSteps, s => [s.id, s.running]),
    [['c1', true]],
  );
});

test('no step at all counts as nothing running', () => {
  const { app } = buildApp();

  assert.equal(app.botStepsRunning(), false);
});

test('bot_step for another conversation is ignored', () => {
  const { app } = buildApp();

  app.handleSSEBotStep({ conversation_id: 'conv-2', call_id: 'c1', html: '<span>Web Search</span>' });

  assert.equal(app.botTyping, false);
  assert.equal(app.botSteps.length, 0);
});

test('bot_step with no active conversation is ignored', () => {
  const { app } = buildApp();
  app.activeConversation = null;

  app.handleSSEBotStep({ conversation_id: 'conv-1', call_id: 'c1', html: '<span>Web Search</span>' });

  assert.equal(app.botTyping, false);
  assert.equal(app.botSteps.length, 0);
});

test('later steps accumulate in order and re-arm the failsafe', () => {
  const { app, timers } = buildApp();

  app.handleSSEBotStep({ conversation_id: 'conv-1', call_id: 'c1', html: '<span>Web Search</span>' });
  app.handleSSEBotStep({ conversation_id: 'conv-1', call_id: 'c2', html: '<span>Calendar</span>' });

  assert.deepStrictEqual(
    Array.from(app.botSteps, s => s.html),
    ['<span>Web Search</span>', '<span>Calendar</span>'],
  );
  assert.equal(timers.fns.length, 2, 'each step arms a fresh failsafe timer');
  assert.equal(timers.cleared, 1, 'the previous timer is cancelled');
});

test('the step list is capped', () => {
  const { app } = buildApp();

  for (let i = 0; i < 40; i++) {
    app.handleSSEBotStep({ conversation_id: 'conv-1', call_id: `c${i}`, html: `<span>${i}</span>` });
  }

  assert.equal(app.botSteps.length, 30);
  assert.equal(app.botSteps[app.botSteps.length - 1].html, '<span>39</span>');
});

test('the failsafe timer hides both the steps and the typing indicator', () => {
  const { app, timers } = buildApp();

  app.handleSSEBotStep({ conversation_id: 'conv-1', call_id: 'c1', html: '<span>Web Search</span>' });
  timers.fns[timers.fns.length - 1]();

  assert.equal(app.botTyping, false);
  assert.equal(app.botSteps.length, 0);
});

test('clearBotStep cancels the timer and drops the steps', () => {
  const { app, timers } = buildApp();

  app.handleSSEBotStep({ conversation_id: 'conv-1', call_id: 'c1', html: '<span>Web Search</span>' });
  app.clearBotStep();

  assert.equal(app.botSteps.length, 0);
  assert.equal(app._botStepTimer, null);
  assert.equal(timers.cleared, 1);
});

test('bot_generating raises the indicator for the conversation on screen', () => {
  const { app } = buildApp();

  app.handleSSEBotGenerating({ conversation_ids: ['conv-1', 'conv-9'] });

  assert.equal(app.botTyping, true);
});

test('bot_generating leaves other conversations alone', () => {
  const { app } = buildApp();

  app.handleSSEBotGenerating({ conversation_ids: ['conv-9'] });

  assert.equal(app.botTyping, false);
});

test('a snapshot without the active conversation lowers a stale indicator', () => {
  // The stream dropped mid-generation and the reply landed while the page
  // was away: the reopened connection replays no message event, so the
  // fresh snapshot is the only thing that can end the bubble.
  const { app, timers } = buildApp();
  app.handleSSEBotStep({ conversation_id: 'conv-1', call_id: 'c1', html: '<span>Web Search</span>' });
  assert.equal(app.botTyping, true);

  app.handleSSEBotGenerating({ conversation_ids: [] });

  assert.equal(app.botTyping, false);
  assert.equal(app.botSteps.length, 0);
  assert.equal(app._botStepTimer, null);
  assert.equal(timers.cleared, 1, 'the failsafe timer is cancelled');
});

test('a snapshot naming only other conversations also lowers the stale indicator', () => {
  const { app } = buildApp();
  app.botTyping = true;

  app.handleSSEBotGenerating({ conversation_ids: ['conv-9'] });

  assert.equal(app.botTyping, false);
});

test('bot_generating announced before a conversation is picked is not lost', () => {
  // The stream connects while the page is still booting, so the announcement
  // can land before activeConversation exists.
  const { app } = buildApp();
  app.activeConversation = null;

  app.handleSSEBotGenerating({ conversation_ids: ['conv-1'] });

  assert.equal(app.botTyping, false);
  assert.ok(app.generatingConversations.has('conv-1'));
});

// Composes the sse + conversations mixins so the restore path in
// selectConversation runs against the real implementation.
function buildAppWithConversations() {
  const timers = { fns: [], cleared: 0 };
  const stubs = {
    setTimeout: (fn) => { timers.fns.push(fn); return timers.fns.length; },
    clearTimeout: (id) => { if (id) timers.cleared++; },
    localStorage: { getItem: () => '', setItem: () => {}, removeItem: () => {} },
    // No #message-list in this harness: the server-rendered flag is absent,
    // so only the announced set can raise the indicator.
    document: { getElementById: () => null },
  };
  const sseCtx = loadScripts(
    [
      'workspace/chat/ui/static/chat/ui/js/threads.js',
      'workspace/chat/ui/static/chat/ui/js/sse.js',
    ],
    stubs,
  );
  const convCtx = loadScript('workspace/chat/ui/static/chat/ui/js/conversations.js', stubs);
  const botCtx = loadScript('workspace/chat/ui/static/chat/ui/js/bot.js', stubs);
  const app = Object.assign(
    {},
    convCtx.chatConversationsMixin(),
    sseCtx.chatThreadsMixin(),
    sseCtx.chatSseMixin(),
    botCtx.chatBotMixin(),
    {
      activeConversation: null,
      pendingFiles: [],
      $nextTick: async () => {},
      loadMessages: async () => {},
      markAsRead: async () => {},
      loadPinnedMessages: async () => {},
      refreshConversationItems: () => {},
    },
  );
  return { app, timers };
}

test('a conversation selected after the announcement still shows the indicator', async () => {
  const { app } = buildAppWithConversations();

  app.handleSSEBotGenerating({ conversation_ids: ['conv-1'] });
  await app.selectConversation({ uuid: 'conv-1' }, false);

  assert.equal(app.botTyping, true);
});

test('selecting a conversation with no generation leaves the indicator down', async () => {
  const { app } = buildAppWithConversations();

  app.handleSSEBotGenerating({ conversation_ids: ['conv-9'] });
  await app.selectConversation({ uuid: 'conv-1' }, false);

  assert.equal(app.botTyping, false);
});

test('an announced generation arms the failsafe so a cancel cannot strand the bubble', () => {
  const { app, timers } = buildApp();

  app.handleSSEBotGenerating({ conversation_ids: ['conv-1'] });
  assert.equal(app.botTyping, true);
  timers.fns.forEach(fn => fn());

  assert.equal(app.botTyping, false);
});

test('a human message does not clear an announced generation', async () => {
  // Only the bot's own message ends a generation.
  const { app } = buildAppWithConversations();
  Object.assign(app, {
    availableBots: [{ user_id: 7 }],
    conversations: [],
    _bumpConversationUnread: () => {},
  });
  app.handleSSEBotGenerating({ conversation_ids: ['conv-1'] });

  await app.handleSSEMessage({
    conversation_id: 'conv-1',
    message: { uuid: 'm1', author: { id: 1 } },
  });

  assert.ok(app.generatingConversations.has('conv-1'));

  await app.handleSSEMessage({
    conversation_id: 'conv-1',
    message: { uuid: 'm2', author: { id: 7 } },
  });

  assert.equal(app.generatingConversations.has('conv-1'), false);
});
