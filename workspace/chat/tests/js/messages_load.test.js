'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

/**
 * The load pipeline delegates the swap itself to alpine-ajax ($ajax): the
 * server partial is merged into per-surface targets and stale overlapping
 * requests are resolved by the library's per-target bookkeeping. What is
 * OURS to pin here is the wiring around it: which URL and targets each load
 * asks for, the pagination state read back after a merge, the scroll
 * restore, the clear-before-load, and the two guards that stay on our side
 * (_vetoStaleMerge and _onAjaxMissing).
 */

function node(id, dataset = {}) {
  return {
    id,
    dataset,
    children: [],
    replaceChildren() {
      this.children = [];
      this.cleared = (this.cleared || 0) + 1;
    },
  };
}

function buildDom() {
  const nodes = {
    'message-list': node('message-list'),
    'message-list-state': node('message-list-state', { hasMore: 'true', firstUuid: 'm0' }),
    'message-list-items': node('message-list-items'),
    'thread-message-list': node('thread-message-list'),
    'thread-message-list-state': node('thread-message-list-state', { hasMore: 'false', firstUuid: 't0' }),
    'thread-message-list-items': node('thread-message-list-items'),
  };
  return {
    nodes,
    document: { getElementById: (id) => nodes[id] || null },
  };
}

function buildApp({ dom, overrides = {}, ajax } = {}) {
  dom = dom || buildDom();
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/messages.js', {
    document: dom.document,
  });
  const app = ctx.chatMessagesMixin();
  const calls = [];
  Object.assign(app, {
    activeConversation: { uuid: 'c1' },
    $refs: {
      messagesContainer: { scrollTop: 0, scrollHeight: 100, clientHeight: 0 },
    },
    $nextTick(fn) {
      if (fn) fn();
    },
    // Default stub reports one merged element, the shape $ajax resolves
    // with when the response actually landed.
    $ajax: ajax || (async (url, options) => {
      calls.push({ url, options });
      return [dom.nodes[options.targets[0]]];
    }),
    ...overrides,
  });
  return { app, dom, calls };
}

// ── Full load ──────────────────────────────────────────────

test('loadMessages swaps the surface load targets from the conversation endpoint', async () => {
  const { app, calls } = buildApp();
  await app.loadMessages();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/chat/c1/messages');
  assert.deepStrictEqual(Array.from(calls[0].options.targets), ['message-list']);
  assert.equal(calls[0].options.focus, false, 'a swap must never steal focus');
  assert.equal(app.loadingMessages, false);
});

test('loadMessages reads pagination state off the state element after the merge', async () => {
  const { app } = buildApp();
  await app.loadMessages();
  assert.equal(app.hasMoreMessages, true);
});

test('loadMessages clears the items wrapper before requesting', async () => {
  // A conversation switch must not show the previous conversation under the
  // spinner - but only the items are cleared: the list element itself is the
  // merge target and has to stay in the DOM.
  const dom = buildDom();
  let clearedBeforeRequest = null;
  const { app } = buildApp({
    dom,
    ajax: async () => {
      clearedBeforeRequest = dom.nodes['message-list-items'].cleared === 1;
      return [dom.nodes['message-list']];
    },
  });
  await app.loadMessages();
  assert.equal(clearedBeforeRequest, true);
});

test('a load that merged nothing leaves pagination state and scroll alone', async () => {
  // $ajax resolves with no merged element when the response was superseded
  // by a newer request or refused by _vetoStaleMerge - the request that won
  // owns the state.
  let scrolled = 0;
  const { app } = buildApp({ ajax: async () => [] });
  app.hasMoreMessages = false;
  app.scrollToBottom = () => { scrolled += 1; };
  await app.loadMessages();
  assert.equal(app.hasMoreMessages, false, 'state must not follow a dead response');
  assert.equal(scrolled, 0);
  assert.equal(app.loadingMessages, false);
});

test('a torn-down surface issues no request at all', async () => {
  // A component can outlive its DOM for a moment (an awaited chain resuming
  // after the panel was destroyed). A request issued then would merge into
  // whatever NEW surface owns the target ids by now.
  const { app, calls } = buildApp();
  app._surfaceGone = () => true;
  await app.loadMessages();
  await app._refreshCurrentMessages();
  app.hasMoreMessages = true;
  await app.loadMoreMessages();
  assert.deepStrictEqual(calls, []);
});

// ── Refresh ────────────────────────────────────────────────

test('_refreshCurrentMessages refetches through the surface hooks, not hard-coded ids', async () => {
  // Regression: it once used the scoped container but a literal
  // `/chat/<conv>/messages`, so a refresh on the thread panel injected the
  // whole conversation into the panel.
  const { app, calls } = buildApp({
    overrides: {
      _messageListId: () => 'thread-message-list',
      _loadTargets() { return ['thread-root-message', 'thread-message-list']; },
      _messagesUrl: (cursor) =>
        `/chat/threads/r1/messages${cursor ? '?before=' + cursor : ''}`,
    },
  });
  await app._refreshCurrentMessages();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/chat/threads/r1/messages');
  assert.deepStrictEqual(Array.from(calls[0].options.targets), ['thread-root-message', 'thread-message-list']);
});

test('_refreshCurrentMessages never clears first, so the view cannot flash empty', async () => {
  const dom = buildDom();
  const { app } = buildApp({ dom });
  await app._refreshCurrentMessages();
  assert.equal(dom.nodes['message-list-items'].cleared, undefined);
});

// ── Pagination ─────────────────────────────────────────────

test('loadMoreMessages pages backwards from the state cursor into state + items', async () => {
  const dom = buildDom();
  const { app, calls } = buildApp({
    dom,
    ajax: async (url, options) => {
      calls.push({ url, options });
      // The merge replaces the state element (fresh cursor) and prepends the
      // older groups into the items wrapper.
      dom.nodes['message-list-state'].dataset = { hasMore: 'false', firstUuid: 'm9' };
      return [dom.nodes['message-list-state'], dom.nodes['message-list-items']];
    },
  });
  app.hasMoreMessages = true;

  await app.loadMoreMessages();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/chat/c1/messages?before=m0');
  assert.deepStrictEqual(
    Array.from(calls[0].options.targets),
    ['message-list-state', 'message-list-items'],
  );
  assert.equal(app.hasMoreMessages, false, 'pagination state follows the response');
  assert.equal(app.loadingMoreMessages, false);
});

test('loadMoreMessages restores the scroll position after the prepend', async () => {
  const dom = buildDom();
  const { app } = buildApp({
    dom,
    ajax: async () => {
      app.$refs.messagesContainer.scrollHeight = 250; // grew by 150
      return [dom.nodes['message-list-state'], dom.nodes['message-list-items']];
    },
  });
  app.hasMoreMessages = true;
  app.$refs.messagesContainer.scrollTop = 10;
  app.$refs.messagesContainer.scrollHeight = 100;

  await app.loadMoreMessages();

  assert.equal(app.$refs.messagesContainer.scrollTop, 150,
    'the viewport must stay on the message it was showing');
});

test('loadMoreMessages leaves the scroll alone when the merge was refused', async () => {
  const { app } = buildApp({ ajax: async () => [] });
  app.hasMoreMessages = true;
  app.$refs.messagesContainer.scrollTop = 10;

  await app.loadMoreMessages();

  assert.equal(app.$refs.messagesContainer.scrollTop, 10);
  assert.equal(app.loadingMoreMessages, false);
});

test('loadMoreMessages without a cursor issues no request', async () => {
  const dom = buildDom();
  delete dom.nodes['message-list-state'].dataset.firstUuid;
  const { app, calls } = buildApp({ dom });
  app.hasMoreMessages = true;
  await app.loadMoreMessages();
  assert.deepStrictEqual(calls, []);
});

// ── Stale-merge veto ───────────────────────────────────────

function mergeEvent(attrs) {
  return {
    detail: {
      content: { getAttribute: (name) => attrs[name] ?? null },
    },
    prevented: false,
    preventDefault() { this.prevented = true; },
  };
}

test('a merge stamped for another conversation is refused', () => {
  // The pane is NOT torn down on a conversation switch, so alpine-ajax's own
  // bookkeeping cannot tell a late response for the previous conversation
  // from a fresh one - the server's data-conversation-uuid stamp can.
  const { app } = buildApp();
  const event = mergeEvent({ 'data-conversation-uuid': 'c2' });
  app._vetoStaleMerge(event);
  assert.equal(event.prevented, true);
});

test('a merge stamped for the active conversation goes through', () => {
  const { app } = buildApp();
  const event = mergeEvent({ 'data-conversation-uuid': 'c1' });
  app._vetoStaleMerge(event);
  assert.equal(event.prevented, false);
});

test('unstamped content is let through', () => {
  // The thread root message carries no stamp; refusing it would strip the
  // root from every thread load.
  const { app } = buildApp();
  const event = mergeEvent({});
  app._vetoStaleMerge(event);
  assert.equal(event.prevented, false);
});

// ── Missing-target guard ───────────────────────────────────

test('a response lacking the target keeps the live list instead of removing it', () => {
  // alpine-ajax's default for a 2xx response without the target id is to
  // REMOVE the live element - a redirect to the login page would silently
  // delete the list.
  const { app } = buildApp();
  const event = {
    detail: { target: { closest: (sel) => (sel === '#messages-container' ? {} : null) } },
    prevented: false,
    preventDefault() { this.prevented = true; },
  };
  app._onAjaxMissing(event);
  assert.equal(event.prevented, true);
});

test('the missing-target guard ignores targets outside this surface', () => {
  // The event bubbles to the app root, which also sees misses from other
  // alpine-ajax swaps (conversation list rows) that rely on the default.
  const { app } = buildApp();
  const event = {
    detail: { target: { closest: () => null } },
    prevented: false,
    preventDefault() { this.prevented = true; },
  };
  app._onAjaxMissing(event);
  assert.equal(event.prevented, false);
});
