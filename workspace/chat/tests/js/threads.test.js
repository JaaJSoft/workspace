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

test('opening a thread with nothing unread does not call the read endpoint', async () => {
  let calls = 0;
  const ctx = load({
    fetch: async () => {
      calls++;
      return { ok: true, json: async () => ({ cleared: 0 }) };
    },
  });
  const app = ctx.chatThreadsMixin();
  app.threadUnreadCounts = {};
  await app.markThreadRead('r1');
  assert.equal(calls, 0);
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
    dispatched.map((e) => [e.type, e.detail.root]),
    [['thread-reply-received', 'r1']],
  );
});

test('a live reply for another thread does not wake the open panel', async () => {
  const { app, dispatched } = buildSseApp({ openThreadRoot: 'r1' });
  await app.handleSSEMessage(reply('r2'));
  assert.deepStrictEqual(dispatched, []);
});
