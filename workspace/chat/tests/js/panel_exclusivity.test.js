'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

/**
 * The three right-hand panels (info, search, thread) share one column, so at
 * most one may be open. Each opener has to close the other two; a rule enforced
 * in only one direction leaves two panels side by side on desktop.
 */
function buildApp(documentOverrides = {}) {
  const ctx = loadScripts(
    [
      'workspace/chat/ui/static/chat/ui/js/panels.js',
      'workspace/chat/ui/static/chat/ui/js/threads.js',
    ],
    {
      getCSRFToken: () => 'csrf-token',
      setTimeout,
      document: {
        querySelectorAll: () => [],
        getElementById: () => null,
        ...documentOverrides,
      },
      fetch: async () => ({ ok: true, json: async () => ({}) }),
    },
  );
  // Same composition order as chatApp().
  const app = { ...ctx.chatPanelsMixin(), ...ctx.chatThreadsMixin() };
  Object.assign(app, {
    activeConversation: { uuid: 'c1' },
    _messageIdPrefix: () => 'msg',
    isBotConversation: () => false,
    loadConversationStats() {},
    loadPinnedMessages() {},
    loadConversationMedia() {},
    $nextTick(fn) {
      if (fn) fn();
    },
    $refs: {},
  });
  return app;
}

const openPanels = (app) => ({
  info: app.showInfoPanel,
  search: app.showSearchPanel,
  thread: app.openThreadRoot !== null,
});

test('opening a thread closes the info and search panels', () => {
  const app = buildApp();
  app.showInfoPanel = true;
  app.showSearchPanel = true;

  app.openThread('r1');

  assert.deepStrictEqual(openPanels(app), { info: false, search: false, thread: true });
});

test('opening the info panel closes an open thread', () => {
  const app = buildApp();
  app.openThreadRoot = 'r1';

  app.openInfoPanel();

  assert.deepStrictEqual(openPanels(app), { info: true, search: false, thread: false });
});

test('opening the search panel closes an open thread', () => {
  const app = buildApp();
  app.openThreadRoot = 'r1';

  app.toggleSearchPanel();

  assert.deepStrictEqual(openPanels(app), { info: false, search: true, thread: false });
});

test('a search hit inside a thread opens that thread to reach it', async () => {
  // A threaded reply is not in the main flow, so paging further back never
  // finds it; the panel has to be opened first.
  const inPanel = { id: 'tmsg-m9', classList: { add() {}, remove() {} }, scrollIntoView() {} };
  const nodes = {};
  const app = buildApp({
    getElementById: (id) => nodes[id] || null,
  });
  let pagedBack = 0;
  app._loadAllAndScrollTo = () => {
    pagedBack++;
  };

  const scrolling = app.scrollToMessage('m9', 'r1');
  // The panel fetches its contents, so the target appears a beat later.
  setTimeout(() => {
    nodes['tmsg-m9'] = inPanel;
  }, 120);
  await scrolling;

  assert.equal(app.openThreadRoot, 'r1', 'the thread is opened');
  assert.equal(pagedBack, 0, 'the main flow is not paged back through');
});

test('a search hit in the main flow does not open any thread', async () => {
  const inFlow = { id: 'msg-m1', classList: { add() {}, remove() {} }, scrollIntoView() {} };
  const app = buildApp({
    getElementById: (id) => (id === 'msg-m1' ? inFlow : null),
  });

  await app.scrollToMessage('m1', null);

  assert.equal(app.openThreadRoot, null);
});

test('closing the search panel leaves the thread alone', () => {
  const app = buildApp();
  app.showSearchPanel = true;
  app.openThreadRoot = 'r1';

  app.toggleSearchPanel();

  assert.equal(app.showSearchPanel, false);
  assert.equal(app.openThreadRoot, 'r1', 'closing a panel must not close another');
});
