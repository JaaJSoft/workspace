'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript, loadScripts, CUSTOM_ELEMENT_STUBS } = require('../../../common/tests/js/loader');

/**
 * Pin the optimistic-message lifecycle contract between chatMessagesMixin and
 * the <chat-message-group> shell element (message_shell.js): sending creates
 * the element with `own` + `pending` set and its properties (body, replyInfo,
 * pendingFiles) assigned BEFORE insertion into the list's items wrapper
 * (inside the wrapper so the next full-list merge replaces it with the real
 * server-rendered message), and the element is removed once that happens (or
 * the send fails).
 *
 * The element's rendered output cannot be exercised here - the node:vm
 * loader has no DOM, so custom-element behaviour is out of reach (the loader
 * says as much). That coverage lives in chat/tests/e2e/test_message_shell.py.
 */
function buildDom() {
  const byId = new Map();
  const inserted = [];
  const container = {
    appendChild(el) {
      // Snapshot the state the element will see in connectedCallback: it
      // reads attributes and properties once, on connect, so anything set
      // after this call would be silently ignored.
      inserted.push({
        el,
        atInsert: {
          attrs: { ...el.attrs },
          id: el.id,
          body: el.body,
          replyInfo: el.replyInfo,
          pendingFiles: el.pendingFiles,
        },
      });
      if (el.id) byId.set(el.id, el);
    },
  };
  const document = {
    createElement(tag) {
      return {
        tagName: tag.toUpperCase(),
        attrs: {},
        id: '',
        setAttribute(name, value) { this.attrs[name] = String(value); },
        remove() { byId.delete(this.id); },
      };
    },
    getElementById(id) {
      if (id === 'message-list-items') return container;
      return byId.get(id) || null;
    },
  };
  return { document, inserted, byId };
}

function buildApp() {
  const dom = buildDom();
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/messages.js', {
    document: dom.document,
  });
  const app = ctx.chatMessagesMixin();
  app.activeConversation = {
    uuid: 'c1',
    members: [{ user: { id: 7, username: 'alice' } }],
  };
  app.currentUserId = 7;
  return { app, ctx, ...dom };
}

test('sending creates a pending own <chat-message-group> in the items wrapper', () => {
  const { app, inserted } = buildApp();
  app._injectOptimisticMessage('_optimistic_1', 'hello there', null, null);

  assert.equal(inserted.length, 1);
  const { atInsert, el } = inserted[0];
  assert.equal(el.tagName, 'CHAT-MESSAGE-GROUP');
  assert.equal(atInsert.id, '_optimistic_1');
  // Rendered as an own message with the pending extras (opacity + spinner)
  assert.ok('own' in atInsert.attrs);
  assert.ok('pending' in atInsert.attrs);
  // The sender's identity drives the avatar column
  assert.equal(atInsert.attrs['author-id'], '7');
  assert.equal(atInsert.attrs['author-username'], 'alice');
  assert.equal(atInsert.body, 'hello there');
});

test('properties pass through raw - the shell renders them as text, not HTML', () => {
  const { app, inserted } = buildApp();
  const files = [{ name: 'doc "final".pdf', type: 'application/pdf', size: 123456 }];
  const reply = { uuid: 'm9', author: 'Bob <script>', body: 'original & text' };
  app._injectOptimisticMessage('_optimistic_2', '<b>bold</b>\nline2', reply, files);

  const { atInsert } = inserted[0];
  // No escaping at this layer: the element builds DOM via textContent, so
  // pre-escaped strings here would render literal entities.
  assert.equal(atInsert.body, '<b>bold</b>\nline2');
  assert.deepEqual(atInsert.replyInfo, reply);
  assert.deepEqual(atInsert.pendingFiles, files);
});

test('a missing items wrapper is a no-op, not a crash', () => {
  const { app, inserted, document } = buildApp();
  document.getElementById = () => null;
  app._injectOptimisticMessage('_optimistic_3', 'hello', null, null);
  assert.equal(inserted.length, 0);
});

test('the element is removed once the real message arrives', () => {
  const { app, byId } = buildApp();
  app._injectOptimisticMessage('_optimistic_4', 'bye', null, null);
  assert.ok(byId.has('_optimistic_4'));

  app._removeOptimisticMessage('_optimistic_4');
  assert.ok(!byId.has('_optimistic_4'));

  // Removing an id that is no longer there is a no-op, not an error
  app._removeOptimisticMessage('_optimistic_4');
});

test('message_shell.js defines the chat-message-group element', () => {
  const defined = [];
  loadScripts(['workspace/chat/ui/static/chat/ui/js/message_shell.js'], {
    ...CUSTOM_ELEMENT_STUBS,
    customElements: {
      get: () => undefined,
      define: (name) => defined.push(name),
    },
  });
  assert.deepEqual(defined, ['chat-message-group']);
});
