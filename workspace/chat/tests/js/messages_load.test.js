'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

/**
 * Minimal DOM stub: container and list nodes looked up by id. innerHTML
 * assignment is recorded so the test can assert what was injected without a
 * real DOM.
 */
function buildDom() {
  const nodes = {
    'messages-container': { innerHTML: '', id: 'messages-container' },
    'message-list': { dataset: { hasMore: 'true', firstUuid: 'm0' } },
    'thread-messages-container': { innerHTML: '', id: 'thread-messages-container' },
    'thread-message-list': { dataset: { hasMore: 'false', firstUuid: 't0' } },
  };
  return {
    nodes,
    document: { getElementById: (id) => nodes[id] || null },
  };
}

/**
 * A response node the mixin can walk: loadMoreMessages parses the fetched HTML
 * and moves the returned list's children into the live list.
 *
 * The move is the load-bearing part of the stub. In a real DOM,
 * `fragment.appendChild(node)` DETACHES the node from its current parent, and
 * that detachment is what terminates the mixin's `while (newList.firstChild)`
 * loop. A stub whose appendChild only copies spins forever.
 */
function listNode(id, { hasMore = 'false', firstUuid = '', children = [] } = {}) {
  const node = {
    id,
    dataset: { hasMore, firstUuid },
    children: [],
    get firstChild() {
      return this.children[0] || null;
    },
    insertBefore(fragment, ref) {
      // splice at the reference's index, in one go: unshifting the children
      // one by one would reverse them, so a page of several older messages
      // would land newest-first and the stub would silently disagree with a
      // real DOM.
      const moved = fragment.children.splice(0);
      for (const child of moved) child.parentNode = this;
      const at = ref ? this.children.indexOf(ref) : this.children.length;
      this.children.splice(at < 0 ? this.children.length : at, 0, ...moved);
    },
  };
  for (const name of children) {
    node.children.push({ name, parentNode: node });
  }
  return node;
}

function documentFragment() {
  return {
    children: [],
    appendChild(child) {
      const siblings = child.parentNode ? child.parentNode.children : null;
      if (siblings) siblings.splice(siblings.indexOf(child), 1);
      child.parentNode = this;
      this.children.push(child);
    },
  };
}

const names = (node) => node.children.map((c) => c.name);

function buildApp({ dom, html, urls, parsed = null, overrides = {} }) {
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/messages.js', {
    document: { ...dom.document, createDocumentFragment: documentFragment },
    Alpine: { initTree() {} },
    DOMParser: class {
      parseFromString() {
        return { getElementById: (id) => (parsed && parsed.id === id ? parsed : null) };
      }
    },
    fetch: async (url) => {
      urls.push(url);
      return { ok: true, text: async () => html };
    },
  });
  const app = ctx.chatMessagesMixin();
  Object.assign(app, {
    activeConversation: { uuid: 'c1' },
    $refs: {
      messagesContainer: { scrollTop: 0, scrollHeight: 0, clientHeight: 0 },
    },
    $nextTick(fn) {
      if (fn) fn();
    },
    ...overrides,
  });
  return app;
}

test('loadMessages fills the main container from the conversation endpoint', async () => {
  const dom = buildDom();
  const urls = [];
  const app = buildApp({ dom, html: '<p>hi</p>', urls });
  await app.loadMessages('c1');
  assert.equal(dom.nodes['messages-container'].innerHTML, '<p>hi</p>');
  assert.deepStrictEqual(urls, ['/chat/c1/messages']);
});

test('loadMessages reads pagination state off the main list node', async () => {
  const dom = buildDom();
  const app = buildApp({ dom, html: '<p>hi</p>', urls: [] });
  await app.loadMessages('c1');
  assert.equal(app.hasMoreMessages, true);
});

test('loadMessages clears the container before injecting', async () => {
  const dom = buildDom();
  dom.nodes['messages-container'].innerHTML = '<p>stale</p>';
  const app = buildApp({ dom, html: '<p>fresh</p>', urls: [] });
  await app.loadMessages('c1');
  assert.equal(dom.nodes['messages-container'].innerHTML, '<p>fresh</p>');
});

test('_refreshCurrentMessages refetches through the surface hook, not a hard-coded url', async () => {
  // Regression: it used the scoped container but a literal
  // `/chat/<conv>/messages`, so a refresh on the thread panel injected the
  // whole conversation into the panel's container.
  const dom = buildDom();
  const urls = [];
  const app = buildApp({
    dom,
    html: '<p>thread</p>',
    urls,
    overrides: {
      _messagesContainerId: () => 'thread-messages-container',
      _messagesUrl: (cursor) =>
        `/chat/threads/r1/messages${cursor ? '?before=' + cursor : ''}`,
    },
  });

  await app._refreshCurrentMessages();

  assert.deepStrictEqual(urls, ['/chat/threads/r1/messages']);
  assert.equal(dom.nodes['thread-messages-container'].innerHTML, '<p>thread</p>');
  assert.equal(
    dom.nodes['messages-container'].innerHTML,
    '',
    'the main flow container must not be touched',
  );
});

test('loadMoreMessages pages backwards from the list cursor and prepends', async () => {
  const dom = buildDom();
  const urls = [];
  const live = listNode('message-list', {
    hasMore: 'true',
    firstUuid: 'm0',
    children: ['existing'],
  });
  dom.nodes['message-list'] = live;
  const app = buildApp({
    dom,
    html: '<ignored/>',
    urls,
    parsed: listNode('message-list', {
      hasMore: 'false',
      firstUuid: 'm9',
      children: ['older-1', 'older-2'],
    }),
  });
  app.hasMoreMessages = true;

  await app.loadMoreMessages();

  assert.deepStrictEqual(urls, ['/chat/c1/messages?before=m0']);
  assert.deepStrictEqual(
    names(live),
    ['older-1', 'older-2', 'existing'],
    'older messages prepend, oldest first',
  );
  assert.equal(app.hasMoreMessages, false, 'pagination state follows the response');
  assert.equal(live.dataset.firstUuid, 'm9', 'the cursor advances to the new first message');
});
