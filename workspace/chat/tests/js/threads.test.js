'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const MIXIN_STUBS = {
  chatUiHelpersMixin: () => ({}),
  chatMessagesMixin: () => ({}),
  chatInputMixin: () => ({}),
  chatRecorderMixin: () => ({}),
};

function load(extra = {}) {
  return loadScript('workspace/chat/ui/static/chat/ui/js/threads.js', {
    ...MIXIN_STUBS,
    getCSRFToken: () => 'csrf-token',
    document: { querySelectorAll: () => [] },
    fetch: async () => ({ ok: true, json: async () => ({ cleared: 0 }) }),
    ...extra,
  });
}

// ── Routing ────────────────────────────────────────────────

test('a plain message goes to the main flow and bumps nothing', () => {
  const out = load().chatThreadRouteTargets(
    { message: { uuid: 'm1', thread_root: null } },
    { openThreadRoot: null, showInline: false },
  );
  assert.deepStrictEqual({ ...out }, { mainFlow: true, panel: false, bumpRoot: null });
});

test('a thread reply stays out of the main flow by default', () => {
  const out = load().chatThreadRouteTargets(
    { message: { uuid: 'm2', thread_root: 'r1' } },
    { openThreadRoot: null, showInline: false },
  );
  assert.deepStrictEqual({ ...out }, { mainFlow: false, panel: false, bumpRoot: 'r1' });
});

test('the inline preference puts a thread reply back in the main flow', () => {
  const out = load().chatThreadRouteTargets(
    { message: { uuid: 'm3', thread_root: 'r1' } },
    { openThreadRoot: null, showInline: true },
  );
  assert.deepStrictEqual({ ...out }, { mainFlow: true, panel: false, bumpRoot: 'r1' });
});

test('a reply reaches an open panel only when the panel is on its thread', () => {
  const ctx = load();
  const onIt = ctx.chatThreadRouteTargets(
    { message: { uuid: 'm4', thread_root: 'r1' } },
    { openThreadRoot: 'r1', showInline: false },
  );
  const elsewhere = ctx.chatThreadRouteTargets(
    { message: { uuid: 'm5', thread_root: 'r2' } },
    { openThreadRoot: 'r1', showInline: false },
  );
  assert.equal(onIt.panel, true);
  assert.equal(elsewhere.panel, false);
});

test('a malformed event does not crash the router', () => {
  const out = load().chatThreadRouteTargets({}, { openThreadRoot: null, showInline: false });
  assert.deepStrictEqual({ ...out }, { mainFlow: true, panel: false, bumpRoot: null });
});

// ── Panel surface hooks ────────────────────────────────────

test('the panel overrides every surface hook so it cannot touch the main flow', () => {
  const panel = load().chatThreadPanel('r1');
  panel.activeConversation = { uuid: 'c1' };
  assert.equal(panel._messagesContainerId(), 'thread-messages-container');
  assert.equal(panel._messageListId(), 'thread-message-list');
  assert.equal(panel._messageIdPrefix(), 'tmsg');
  assert.equal(panel._messagesUrl(null), '/chat/threads/r1/messages');
  assert.equal(panel._messagesUrl('t9'), '/chat/threads/r1/messages?before=t9');
  assert.deepStrictEqual(
    Array.from(panel._loadTargets()),
    ['thread-root-message', 'thread-message-list'],
    'a thread load also swaps the root, rendered outside the paginated list',
  );
});

test('writing in the panel tells the main flow to repaint', async () => {
  // SSE never echoes your own message back to you, so nothing else would tell
  // the conversation that its reply count changed - or, with the inline
  // preference on, that a new reply belongs in the flow. Without this the user
  // has to reload the page to see either.
  const dispatched = [];
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/threads.js', {
    ...MIXIN_STUBS,
    chatMessagesMixin: () => ({
      _refreshCurrentMessages: async function () {
        this._panelRefreshed = true;
      },
    }),
    getCSRFToken: () => 'csrf-token',
    document: { querySelectorAll: () => [] },
    CustomEvent: class {
      constructor(type) {
        this.type = type;
      }
    },
    dispatchEvent: (e) => dispatched.push(e.type),
    fetch: async () => ({ ok: true, json: async () => ({}) }),
  });

  const panel = ctx.chatThreadPanel('r1');
  await panel._refreshCurrentMessages();

  assert.equal(panel._panelRefreshed, true, 'the panel still refreshes itself');
  assert.deepStrictEqual(dispatched, ['chat:refresh-messages']);
});

test('replying to nothing in particular answers the thread root', () => {
  const panel = load().chatThreadPanel('r1');
  panel.replyingTo = null;
  assert.equal(panel._replyTarget(), 'r1');
  panel.replyingTo = { uuid: 'm7' };
  assert.equal(panel._replyTarget(), 'm7');
});

// ── Unread bookkeeping ─────────────────────────────────────

test('an incoming reply bumps the unread counter of a thread that is not open', () => {
  const app = load().chatThreadsMixin();
  app.openThreadRoot = null;
  app.bumpThreadUnread('r1');
  app.bumpThreadUnread('r1');
  assert.equal(app.threadUnread('r1'), 2);
});

test('an incoming reply does not bump the thread the user is reading', () => {
  const app = load().chatThreadsMixin();
  app.openThreadRoot = 'r1';
  app.bumpThreadUnread('r1');
  assert.equal(app.threadUnread('r1'), 0);
});

test('opening a thread clears its counter and closes the other panels', () => {
  const app = load().chatThreadsMixin();
  app.showInfoPanel = true;
  let searchClosed = false;
  app.closeSearchPanel = () => {
    searchClosed = true;
  };
  app.threadUnreadCounts = { r1: 4 };

  app.openThread('r1');

  assert.equal(app.openThreadRoot, 'r1');
  assert.equal(app.threadUnread('r1'), 0);
  assert.equal(app.showInfoPanel, false, 'opening a thread closes the info panel');
  assert.equal(searchClosed, true, 'opening a thread closes the search panel');
});

test('opening a thread clears it server-side even with no locally seen backlog', async () => {
  // The local counter only holds what this page session saw over SSE and
  // starts empty, so skipping the POST when it reads zero would leave a
  // backlog from before the page loaded unread forever.
  const urls = [];
  const ctx = load({
    fetch: async (url, opts) => {
      urls.push([url, opts.method]);
      return { ok: true, json: async () => ({ cleared: 4 }) };
    },
  });
  const app = ctx.chatThreadsMixin();
  app.threadUnreadCounts = {};
  await app.markThreadRead('r1');
  assert.deepStrictEqual(urls, [['/api/v1/chat/threads/r1/read', 'POST']]);
});

test('opening a thread with a backlog posts to the read endpoint', async () => {
  const urls = [];
  const ctx = load({
    fetch: async (url, opts) => {
      urls.push([url, opts.method]);
      return { ok: true, json: async () => ({ cleared: 4 }) };
    },
  });
  const app = ctx.chatThreadsMixin();
  app.threadUnreadCounts = { r1: 4 };
  await app.markThreadRead('r1');
  assert.deepStrictEqual(urls, [['/api/v1/chat/threads/r1/read', 'POST']]);
});

test('closing a thread leaves the panel with no root', () => {
  const app = load().chatThreadsMixin();
  app.openThreadRoot = 'r1';
  app.closeThread();
  assert.equal(app.openThreadRoot, null);
});

// ── Rendered counter ───────────────────────────────────────

function labelNode(uuid, text) {
  return { dataset: { threadCount: uuid }, textContent: text };
}

test('the rendered reply counter grows on every copy of the root', () => {
  const labels = [labelNode('r1', '2 replies'), labelNode('r1', '2 replies')];
  const ctx = load({
    document: {
      querySelectorAll: (sel) => (sel.includes('r1') ? labels : []),
    },
  });
  ctx.chatThreadsMixin()._bumpRenderedReplyCount('r1');
  assert.deepStrictEqual(
    labels.map((l) => l.textContent),
    ['3 replies', '3 replies'],
  );
});

test('the counter pluralises correctly on the first reply', () => {
  const label = labelNode('r1', '0 replies');
  const ctx = load({ document: { querySelectorAll: () => [label] } });
  ctx.chatThreadsMixin()._bumpRenderedReplyCount('r1');
  assert.equal(label.textContent, '1 reply');
});

test('a root that is not on screen is simply skipped', () => {
  const ctx = load({ document: { querySelectorAll: () => [] } });
  assert.doesNotThrow(() => ctx.chatThreadsMixin()._bumpRenderedReplyCount('r9'));
});

// ── SSE delivery ───────────────────────────────────────────

const { loadScripts } = require('../../../common/tests/js/loader');

/**
 * chatApp's SSE handler with the threads mixin composed in, as the real page
 * composes them, and every collaborator it calls stubbed out.
 */
function buildSseApp({ openThreadRoot = null, showInline = false } = {}) {
  const dispatched = [];
  const ctx = loadScripts(
    [
      'workspace/chat/ui/static/chat/ui/js/threads.js',
      'workspace/chat/ui/static/chat/ui/js/sse.js',
    ],
    {
      ...MIXIN_STUBS,
      getCSRFToken: () => 'csrf-token',
      document: { querySelectorAll: () => [], getElementById: () => null },
      fetch: async () => ({ ok: true, json: async () => ({ cleared: 0 }) }),
      CustomEvent: class {
        constructor(type, init) {
          this.type = type;
          this.detail = init?.detail;
        }
      },
    },
  );
  ctx.dispatchEvent = (e) => dispatched.push(e);

  const counters = { refreshed: 0, scrolled: 0, read: 0 };
  const app = { ...ctx.chatThreadsMixin(), ...ctx.chatSseMixin() };
  Object.assign(app, {
    openThreadRoot,
    chatPrefs: { showThreadRepliesInline: showInline },
    activeConversation: { uuid: 'c1' },
    botTyping: false,
    isBotMessage: () => false,
    clearBotStep() {},
    // Provided by chatMessagesMixin on the real component.
    _messageIdPrefix: () => 'msg',
    _animateMessageEntry() {},
    _isNearBottom: () => true,
    async _refreshCurrentMessages() {
      counters.refreshed++;
    },
    scrollToBottom() {
      counters.scrolled++;
    },
    async markAsRead() {
      counters.read++;
    },
    _updateConversationLastMessage() {},
    _bumpConversationUnread() {},
    refreshConversationItems() {},
  });
  return { app, counters, dispatched };
}

const reply = (threadRoot) => ({
  conversation_id: 'c1',
  message: { uuid: 'm9', thread_root: threadRoot },
});

test('a live thread reply does not disturb the main flow', async () => {
  const { app, counters } = buildSseApp();
  await app.handleSSEMessage(reply('r1'));
  assert.equal(counters.refreshed, 0, 'the main flow must not refetch');
  assert.equal(app.threadUnread('r1'), 1, 'the thread counter grows instead');
});

test('a live plain message still refreshes the main flow', async () => {
  const { app, counters } = buildSseApp();
  await app.handleSSEMessage(reply(null));
  assert.equal(counters.refreshed, 1);
});

test('the inline preference lets a live thread reply into the main flow', async () => {
  const { app, counters } = buildSseApp({ showInline: true });
  await app.handleSSEMessage(reply('r1'));
  assert.equal(counters.refreshed, 1);
});

test('a live reply reaches an open panel through a window event', async () => {
  const { app, dispatched } = buildSseApp({ openThreadRoot: 'r1' });
  await app.handleSSEMessage(reply('r1'));
  assert.deepStrictEqual(
    dispatched.map((e) => [e.type, e.detail.root, e.detail.uuid]),
    [['thread-reply-received', 'r1', 'm9']],
  );
});

test('a live reply for another thread does not wake the open panel', async () => {
  const { app, dispatched } = buildSseApp({ openThreadRoot: 'r1' });
  await app.handleSSEMessage(reply('r2'));
  assert.deepStrictEqual(dispatched, []);
});

// ── Switching threads ──────────────────────────────────────

test('opening a second thread bounces the root through null so x-if remounts', () => {
  // x-if only tears down through a falsy value: switching straight from one
  // root to another keeps the component built for the first thread, panel
  // stuck on it. openThread must pass through null and re-set on nextTick.
  const app = load().chatThreadsMixin();
  const ticks = [];
  app.$nextTick = (fn) => ticks.push(fn);
  app.closeSearchPanel = () => {};
  app.showInfoPanel = false;

  app.openThread('r1');
  assert.equal(app.openThreadRoot, 'r1');

  app.openThread('r2');
  assert.equal(app.openThreadRoot, null, 'the panel unmounts first');
  ticks.forEach((fn) => fn());
  assert.equal(app.openThreadRoot, 'r2', 'then remounts on the new root');
});

test('reopening the thread already shown does not remount the panel', () => {
  const app = load().chatThreadsMixin();
  let bounced = false;
  app.$nextTick = () => {
    bounced = true;
  };
  app.closeSearchPanel = () => {};

  app.openThread('r1');
  app.openThread('r1');

  assert.equal(app.openThreadRoot, 'r1');
  assert.equal(bounced, false, 'same root must not tear the panel down');
});

test('a live reply landing in the open panel is marked read server-side', async () => {
  // The server counted the reply on the participant row and the conversation
  // badge before pushing it over SSE; the user is looking at it, so only the
  // read endpoint can settle those counters again.
  const { app, urls } = buildSseAppCapturingFetch({ openThreadRoot: 'r1' });
  await app.handleSSEMessage(reply('r1'));
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepStrictEqual(urls, [['/api/v1/chat/threads/r1/read', 'POST']]);
  assert.equal(app.threadUnread('r1'), 0, 'no local unread survives either');
});

test('a live reply to a closed thread is not marked read', async () => {
  const { app, urls } = buildSseAppCapturingFetch({ openThreadRoot: null });
  await app.handleSSEMessage(reply('r1'));
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepStrictEqual(urls, []);
  assert.equal(app.threadUnread('r1'), 1);
});

function buildSseAppCapturingFetch({ openThreadRoot = null } = {}) {
  const urls = [];
  const ctx = loadScripts(
    [
      'workspace/chat/ui/static/chat/ui/js/threads.js',
      'workspace/chat/ui/static/chat/ui/js/sse.js',
    ],
    {
      ...MIXIN_STUBS,
      getCSRFToken: () => 'csrf-token',
      document: { querySelectorAll: () => [], getElementById: () => null },
      fetch: async (url, opts) => {
        urls.push([url, opts.method]);
        return { ok: true, json: async () => ({ cleared: 0 }) };
      },
      CustomEvent: class {
        constructor(type, init) {
          this.type = type;
          this.detail = init?.detail;
        }
      },
    },
  );
  ctx.dispatchEvent = () => {};

  const app = { ...ctx.chatThreadsMixin(), ...ctx.chatSseMixin() };
  Object.assign(app, {
    openThreadRoot,
    chatPrefs: { showThreadRepliesInline: false },
    activeConversation: { uuid: 'c1' },
    botTyping: false,
    isBotMessage: () => false,
    clearBotStep() {},
    _messageIdPrefix: () => 'msg',
    _animateMessageEntry() {},
    _isNearBottom: () => true,
    async _refreshCurrentMessages() {},
    scrollToBottom() {},
    async markAsRead() {},
    _updateConversationLastMessage() {},
    _bumpConversationUnread() {},
    refreshConversationItems() {},
  });
  return { app, urls };
}

// ── Conversation switching ─────────────────────────────────

test('switching conversations closes the thread panel', async () => {
  // A thread belongs to the conversation it was opened in: left open across a
  // switch, writing in the panel would post a reply into the wrong
  // conversation (its reply_to lives elsewhere, the endpoint 400s).
  const ctx = loadScripts(
    [
      'workspace/chat/ui/static/chat/ui/js/threads.js',
      'workspace/chat/ui/static/chat/ui/js/conversations.js',
    ],
    {
      ...MIXIN_STUBS,
      getCSRFToken: () => 'csrf-token',
      document: { querySelectorAll: () => [], getElementById: () => null },
      fetch: async () => ({ ok: true, json: async () => ({}) }),
      localStorage: { getItem: () => '', setItem() {}, removeItem() {} },
      history: { pushState() {} },
      location: { pathname: '/chat' },
      URL: { revokeObjectURL() {} },
    },
  );

  const app = { ...ctx.chatConversationsMixin(), ...ctx.chatThreadsMixin() };
  Object.assign(app, {
    activeConversation: { uuid: 'c1' },
    messageBody: '',
    pendingFiles: [],
    generatingConversations: new Set(),
    $nextTick: async (fn) => {
      if (fn) fn();
    },
    async loadMessages() {},
    async markAsRead() {},
    async loadPinnedMessages() {},
    scrollToBottom() {},
    getMessageInput: () => null,
  });
  app.openThreadRoot = 'r1';

  await app.selectConversation({ uuid: 'c2' }, false);

  assert.equal(app.openThreadRoot, null, 'the previous conversation\'s thread is closed');
});

// ── Stale responses and cross-surface reactions ────────────

test('a destroyed panel issues no request that could land in its successor', async () => {
  // Open thread A, open thread B while A's component is still awaiting
  // something: both panels share one conversation and one set of target ids,
  // so a request issued by A's dead component would resolve those ids to B's
  // fresh DOM and merge A's thread into B's panel. (A response already in
  // flight when the panel dies is alpine-ajax's side of the bargain: its
  // targets were resolved at request time and left the document with the
  // panel.)
  const ctx = loadScripts(
    ['workspace/chat/ui/static/chat/ui/js/messages.js', 'workspace/chat/ui/static/chat/ui/js/threads.js'],
    {
      chatUiHelpersMixin: () => ({}),
      chatInputMixin: () => ({}),
      chatRecorderMixin: () => ({}),
      getCSRFToken: () => 'csrf-token',
      document: { querySelectorAll: () => [], getElementById: () => null },
      CustomEvent: class {
        constructor(type) {
          this.type = type;
        }
      },
      dispatchEvent: () => {},
    },
  );

  const panelA = ctx.chatThreadPanel('rA');
  panelA.activeConversation = { uuid: 'c1' };
  panelA.scrollToBottom = () => {};
  const requests = [];
  panelA.$ajax = async (url) => {
    requests.push(url);
    return [];
  };

  panelA.destroy();
  await panelA.loadMessages();
  await panelA.refreshPanelOnly();
  panelA.hasMoreMessages = true;
  await panelA.loadMoreMessages();

  assert.deepStrictEqual(requests, [],
    "thread A's dead component must not fetch into thread B's panel");
});

test('a reaction in the main flow tells the panel to repaint', async () => {
  const dispatched = [];
  const ctx = loadScripts(
    ['workspace/chat/ui/static/chat/ui/js/messages.js'],
    {
      getCSRFToken: () => 'csrf-token',
      document: { querySelectorAll: () => [], getElementById: () => null },
      fetch: async () => ({ ok: true, text: async () => '' }),
      CustomEvent: class {
        constructor(type) {
          this.type = type;
        }
      },
      dispatchEvent: (e) => dispatched.push(e.type),
    },
  );
  const app = ctx.chatMessagesMixin();
  app.activeConversation = { uuid: 'c1' };
  app._refreshCurrentMessages = async () => {};

  await app.toggleReaction('m1', '👍');

  assert.deepStrictEqual(dispatched, ['chat:refresh-thread']);
});

test('a reaction in the panel does not echo back into a second panel fetch', async () => {
  const dispatched = [];
  const ctx = loadScripts(
    ['workspace/chat/ui/static/chat/ui/js/messages.js', 'workspace/chat/ui/static/chat/ui/js/threads.js'],
    {
      chatUiHelpersMixin: () => ({}),
      chatInputMixin: () => ({}),
      chatRecorderMixin: () => ({}),
      getCSRFToken: () => 'csrf-token',
      document: { querySelectorAll: () => [], getElementById: () => null },
      fetch: async () => ({ ok: true, text: async () => '' }),
      CustomEvent: class {
        constructor(type) {
          this.type = type;
        }
      },
      dispatchEvent: (e) => dispatched.push(e.type),
    },
  );
  const panel = ctx.chatThreadPanel('r1');
  panel.activeConversation = { uuid: 'c1' };

  await panel.toggleReaction('m1', '👍');

  // The panel's own refresh dispatches chat:refresh-messages (main flow
  // repaint); chat:refresh-thread would make the panel fetch itself twice.
  assert.deepStrictEqual(dispatched, ['chat:refresh-messages']);
});

// ── Re-review findings ─────────────────────────────────────

test('the edit shortcut resolves the panel surface prefix', () => {
  // editLastOwnMessage hard-coded 'msg-': on the panel a bubble is tmsg-<uuid>,
  // so the stripped id kept a leading 't' and startEdit found nothing.
  const bubble = {
    id: 'tmsg-m7',
    closest: (sel) => (sel === '.msg-group-end' ? {} : null),
  };
  const ctx = loadScripts(
    ['workspace/chat/ui/static/chat/ui/js/messages.js', 'workspace/chat/ui/static/chat/ui/js/threads.js'],
    {
      chatUiHelpersMixin: () => ({}),
      chatInputMixin: () => ({}),
      chatRecorderMixin: () => ({}),
      getCSRFToken: () => 'csrf-token',
      document: {
        querySelectorAll: () => [],
        getElementById: (id) =>
          id === 'thread-messages-container'
            ? { querySelectorAll: () => [bubble] }
            : null,
      },
      fetch: async () => ({ ok: true, text: async () => '' }),
      CustomEvent: class {},
      dispatchEvent: () => {},
    },
  );
  const panel = ctx.chatThreadPanel('r1');
  const edited = [];
  panel.startEdit = (uuid) => edited.push(uuid);

  panel.editLastOwnMessage();

  assert.deepStrictEqual(edited, ['m7']);
});

test('overlapping panel loads and refreshes contend for the same targets', async () => {
  // A thread-reply reload and a reaction repaint can overlap on the same live
  // panel: same component, same conversation, nothing torn down. alpine-ajax
  // arbitrates that race per target element - only the most recently issued
  // request for an element is merged - which protects the panel only as long
  // as every full swap the panel makes aims at the SAME target ids. This
  // pins that structural precondition.
  const ctx = loadScripts(
    ['workspace/chat/ui/static/chat/ui/js/messages.js', 'workspace/chat/ui/static/chat/ui/js/threads.js'],
    {
      chatUiHelpersMixin: () => ({}),
      chatInputMixin: () => ({}),
      chatRecorderMixin: () => ({}),
      getCSRFToken: () => 'csrf-token',
      document: {
        querySelectorAll: () => [],
        getElementById: () => null,
      },
      CustomEvent: class {},
      dispatchEvent: () => {},
    },
  );
  const panel = ctx.chatThreadPanel('r1');
  panel.activeConversation = { uuid: 'c1' };
  panel.scrollToBottom = () => {};
  const targeted = [];
  panel.$ajax = async (url, options) => {
    targeted.push(options.targets);
    return [];
  };

  await panel.loadMessages();
  await panel.refreshPanelOnly();

  assert.equal(targeted.length, 2);
  assert.deepStrictEqual(targeted[0], targeted[1],
    'load and refresh must contend for the same elements, or neither race is arbitrated');
});

test('a deep link to another conversation closes the panel instead of misleading', async () => {
  // /chat/<A>?thread=<root of B> passes the server's membership check as long
  // as the user belongs to B - the panel itself has to notice the thread does
  // not belong to the conversation it sits in.
  const buildPanel = (listConversation) => {
    const ctx = loadScripts(
      ['workspace/chat/ui/static/chat/ui/js/messages.js', 'workspace/chat/ui/static/chat/ui/js/threads.js'],
      {
        chatUiHelpersMixin: () => ({}),
        chatInputMixin: () => ({}),
        chatRecorderMixin: () => ({}),
        getCSRFToken: () => 'csrf-token',
        document: {
          querySelectorAll: () => [],
          getElementById: (id) =>
            id === 'thread-message-list'
              ? { dataset: { conversationUuid: listConversation, hasMore: 'false' } }
              : null,
        },
        CustomEvent: class {},
        dispatchEvent: () => {},
      },
    );
    const panel = ctx.chatThreadPanel('r1');
    panel.activeConversation = { uuid: 'c1' };
    panel.scrollToBottom = () => {};
    panel.$ajax = async () => [];
    panel.$nextTick = () => {};
    panel.$el = null;
    panel.closed = 0;
    panel.closeThread = () => {
      panel.closed += 1;
    };
    return panel;
  };

  const foreign = buildPanel('c2');
  await foreign.init();
  assert.equal(foreign.closed, 1, "a thread from another conversation closes");

  const local = buildPanel('c1');
  await local.init();
  assert.equal(local.closed, 0, 'a matching thread stays open');
});
