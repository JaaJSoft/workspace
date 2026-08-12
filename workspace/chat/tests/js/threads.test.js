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
