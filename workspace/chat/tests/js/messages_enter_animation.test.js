'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

/**
 * Pin which element gets the `msg-enter` class (the CSS entrance animation in
 * chat.css). Every refresh replaces the whole list, so the class must land on
 * the one bubble that is actually new - never on the bubbles re-rendered
 * around it - and on the optimistic bubble of a send, which the real bubble
 * later replaces without animating again.
 */
function makeElement(id) {
  const classes = new Set();
  return {
    id,
    attrs: {},
    classList: {
      add(...names) { names.forEach((n) => classes.add(n)); },
      contains(name) { return classes.has(name); },
    },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    remove() {},
  };
}

function buildDom() {
  const byId = new Map();
  const container = {
    appendChild(el) { if (el.id) byId.set(el.id, el); },
  };
  const document = {
    createElement: () => makeElement(''),
    getElementById(id) {
      if (id === 'message-list-items') return container;
      return byId.get(id) || null;
    },
  };
  return { document, byId };
}

function buildApp() {
  const dom = buildDom();
  const ctx = loadScripts(
    [
      'workspace/chat/ui/static/chat/ui/js/threads.js',
      'workspace/chat/ui/static/chat/ui/js/messages.js',
      'workspace/chat/ui/static/chat/ui/js/sse.js',
    ],
    { document: dom.document, clearTimeout: () => {} },
  );
  const app = { ...ctx.chatMessagesMixin(), ...ctx.chatSseMixin(), ...ctx.chatThreadsMixin() };
  Object.assign(app, {
    activeConversation: { uuid: 'c1', members: [{ user: { id: 7, username: 'alice' } }] },
    currentUserId: 7,
    chatPrefs: {},
    botTyping: false,
    isBotMessage: () => false,
    _isNearBottom: () => true,
    scrollToBottom() {},
    async markAsRead() {},
    _updateConversationLastMessage() {},
    _bumpConversationUnread() {},
    refreshConversationItems() {},
    // Stand-in for the alpine-ajax merge: the server partial lands with the
    // old bubbles re-rendered and the new one appended.
    async _refreshCurrentMessages() {
      for (const id of ['msg-old', 'msg-new']) dom.byId.set(id, makeElement(id));
    },
  });
  return { app, ...dom };
}

test('an incoming message animates its own bubble only, not the re-rendered ones', async () => {
  const { app, byId } = buildApp();

  await app.handleSSEMessage({ conversation_id: 'c1', message: { uuid: 'new', author: { id: 3 } } });

  assert.ok(byId.get('msg-new').classList.contains('msg-enter'), 'the new bubble should animate in');
  assert.ok(!byId.get('msg-old').classList.contains('msg-enter'), 'a re-rendered bubble must stay still');
});

test('a message already rendered is left alone', async () => {
  const { app, byId } = buildApp();
  byId.set('msg-new', makeElement('msg-new'));

  await app.handleSSEMessage({ conversation_id: 'c1', message: { uuid: 'new', author: { id: 3 } } });

  assert.ok(!byId.get('msg-new').classList.contains('msg-enter'));
});

test('the optimistic bubble of a send carries the entrance class at insertion', () => {
  const { app, byId } = buildApp();

  app._injectOptimisticMessage('_optimistic_1', 'hello', null, null);

  assert.ok(byId.get('_optimistic_1').classList.contains('msg-enter'));
});
