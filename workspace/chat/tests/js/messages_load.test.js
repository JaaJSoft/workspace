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
    'message-list-state': node('message-list-state', { offset: '70', end: '120', hasNewer: 'false' }),
    'message-list-items': node('message-list-items'),
    'thread-message-list': node('thread-message-list'),
    'thread-message-list-state': node('thread-message-list-state', { offset: '0', end: '3', hasNewer: 'false' }),
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
  assert.equal(calls[0].url, '/chat/c1/messages?limit=50');
  assert.deepStrictEqual(Array.from(calls[0].options.targets), ['message-list']);
  assert.equal(calls[0].options.focus, false, 'a swap must never steal focus');
  assert.equal(app.loadingMessages, false);
});

test('loadMessages reads where the page sits off the state element after the merge', async () => {
  const { app } = buildApp();
  await app.loadMessages();
  assert.equal(app.hasMoreMessages, true, 'rows above offset 70');
  assert.equal(app.hasNewerMessages, false);
  assert.equal(app._windowStart, 70);
  assert.equal(app._windowEnd, 120);
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
      _messagesBaseUrl: () => '/chat/threads/r1/messages',
    },
  });
  await app._refreshCurrentMessages();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/chat/threads/r1/messages?limit=50');
  assert.deepStrictEqual(Array.from(calls[0].options.targets), ['thread-root-message', 'thread-message-list']);
});

test('_refreshCurrentMessages never clears first, so the view cannot flash empty', async () => {
  const dom = buildDom();
  const { app } = buildApp({ dom });
  await app._refreshCurrentMessages();
  assert.equal(dom.nodes['message-list-items'].cleared, undefined);
});

// ── Pagination ─────────────────────────────────────────────

test('loadMoreMessages asks for the page right above the window, prepended into state + items', async () => {
  const dom = buildDom();
  const { app, calls } = buildApp({
    dom,
    ajax: async (url, options) => {
      calls.push({ url, options, merge: app.itemsMerge });
      return [dom.nodes['message-list-state'], dom.nodes['message-list-items']];
    },
  });
  await app.loadMessages();
  calls.length = 0;
  dom.nodes['message-list-state'].dataset = { offset: '20', end: '70', hasNewer: 'true' };

  await app.loadMoreMessages();

  assert.equal(calls[0].url, '/chat/c1/messages?limit=50&offset=20');
  assert.deepStrictEqual(
    Array.from(calls[0].options.targets),
    ['message-list-state', 'message-list-items'],
  );
  assert.equal(calls[0].merge, 'prepend');
  assert.equal(app._windowStart, 20);
  assert.equal(app._windowEnd, 120, 'an older page leaves the end of the window alone');
  assert.equal(app.hasNewerMessages, false, 'nor what is known about the present');
  assert.equal(app.loadingMoreMessages, false);
});

test('loadMoreMessages never overlaps the window near the top', async () => {
  const { app, calls } = buildApp();
  app.hasMoreMessages = true;
  app._windowStart = 12;
  await app.loadMoreMessages();
  assert.equal(calls[0].url, '/chat/c1/messages?limit=12&offset=0');
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
  app._windowStart = 70;
  app.$refs.messagesContainer.scrollTop = 10;
  app.$refs.messagesContainer.scrollHeight = 100;

  await app.loadMoreMessages();

  assert.equal(app.$refs.messagesContainer.scrollTop, 150,
    'the viewport must stay on the message it was showing');
});

test('loadMoreMessages leaves the scroll alone when the merge was refused', async () => {
  const { app } = buildApp({ ajax: async () => [] });
  app.hasMoreMessages = true;
  app._windowStart = 70;
  app.$refs.messagesContainer.scrollTop = 10;

  await app.loadMoreMessages();

  assert.equal(app.$refs.messagesContainer.scrollTop, 10);
  assert.equal(app.loadingMoreMessages, false);
});

test('loadMoreMessages at the top of the list issues no request', async () => {
  const { app, calls } = buildApp();
  app.hasMoreMessages = true;
  app._windowStart = 0;
  await app.loadMoreMessages();
  assert.deepStrictEqual(calls, []);
});

test('a day split across two pages keeps its first separator only', () => {
  const removed = [];
  const divider = (day) => ({ dataset: { day }, remove() { removed.push(day); } });
  const dividers = [divider('2024-03-01'), divider('2024-03-02'), divider('2024-03-02'), divider('2024-03-03')];
  const dom = buildDom();
  dom.nodes['message-list-items'].querySelectorAll = () => dividers;
  const { app } = buildApp({ dom });
  app._dropRepeatedDays();
  assert.deepStrictEqual(removed, ['2024-03-02']);
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

// ── Links to a message, and newer pages ─────────────────

test('loadMessages({ message }) opens the list on the message and keeps the scroll off the bottom', async () => {
  const dom = buildDom();
  dom.nodes['message-list-state'].dataset = {
    offset: '10', end: '60', hasNewer: 'true', targetUuid: 'm35',
  };
  let scrolled = 0;
  const { app, calls } = buildApp({ dom });
  app.scrollToBottom = () => { scrolled += 1; };

  const shown = await app.loadMessages({ message: 'm35' });

  assert.equal(calls[0].url, '/chat/c1/messages?limit=50&message=m35');
  assert.equal(shown.targetUuid, 'm35');
  assert.equal(shown.targetThreadRoot, null);
  assert.equal(app.hasNewerMessages, true);
  assert.equal(scrolled, 0, 'the message is shown, not the end of the page');
});

test('a link that found nothing falls back to the last page at the bottom', async () => {
  // Unknown and out of reach answer alike: no target on the state element.
  let scrolled = 0;
  const { app } = buildApp();
  app.scrollToBottom = () => { scrolled += 1; };

  const shown = await app.loadMessages({ message: 'gone' });

  assert.equal(shown.targetUuid, null);
  assert.equal(scrolled, 1);
});

test('loadNewerMessages asks for the page right below the window, appended into state + items', async () => {
  const dom = buildDom();
  const { app, calls } = buildApp({
    dom,
    ajax: async (url, options) => {
      calls.push({ url, options, merge: app.itemsMerge });
      dom.nodes['message-list-state'].dataset = { offset: '60', end: '110', hasNewer: 'false' };
      return [dom.nodes['message-list-state']];
    },
  });
  app.hasNewerMessages = true;
  app.hasMoreMessages = true;
  app._windowStart = 10;
  app._windowEnd = 60;

  await app.loadNewerMessages();

  assert.equal(calls[0].url, '/chat/c1/messages?limit=50&offset=60');
  assert.deepStrictEqual(Array.from(calls[0].options.targets), ['message-list-state', 'message-list-items']);
  assert.equal(calls[0].merge, 'append');
  assert.equal(app._windowEnd, 110);
  assert.equal(app._windowStart, 10, 'a newer page leaves the top of the window alone');
  assert.equal(app.hasMoreMessages, true);
  assert.equal(app.hasNewerMessages, false, 'the present is reached');
  assert.equal(app.loadingNewerMessages, false);
});

test('loadNewerMessages at the present issues no request', async () => {
  const { app, calls } = buildApp();
  app.hasNewerMessages = false;
  await app.loadNewerMessages();
  assert.deepStrictEqual(calls, []);
});

test('scrolling to the end of a stretch of the past loads the newer page', () => {
  const { app } = buildApp();
  let loaded = 0;
  app.loadNewerMessages = () => { loaded += 1; };
  Object.assign(app.$refs.messagesContainer, { scrollTop: 400, scrollHeight: 1000, clientHeight: 580 });
  app.hasNewerMessages = true;

  app.handleScroll();

  assert.equal(loaded, 1);
});

test('a refresh away from the present reloads the rows on screen', async () => {
  // The last page is not what is on screen: reloading it would take the
  // reader away from the past they opened.
  const { app, calls } = buildApp();
  app.hasNewerMessages = true;
  app._windowStart = 10;
  app._windowEnd = 110;
  app._visibleMessage = () => ({ uuid: 'm40', offset: 12 });
  const restored = [];
  app._restoreVisibleMessage = (visible) => restored.push(visible.uuid);

  await app._refreshCurrentMessages();

  assert.equal(calls[0].url, '/chat/c1/messages?offset=10&limit=100');
  assert.deepStrictEqual(restored, ['m40']);
});

test('a refresh at the present reloads the last page', async () => {
  const { app, calls } = buildApp();
  app.hasNewerMessages = false;
  app._visibleMessage = () => { throw new Error('not needed at the present'); };
  await app._refreshCurrentMessages();
  assert.equal(calls[0].url, '/chat/c1/messages?limit=50');
});

test('messageLink names the conversation and the message', () => {
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/messages.js', {
    location: { origin: 'https://ws.example' },
  });
  const app = ctx.chatMessagesMixin();
  app.activeConversation = { uuid: 'c1' };
  assert.equal(app.messageLink('m7'), 'https://ws.example/chat/c1?message=m7');
});
