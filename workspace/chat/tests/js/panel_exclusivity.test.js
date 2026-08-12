'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

/**
 * The three right-hand panels (info, search, thread) share one column, so at
 * most one may be open. Each opener has to close the other two; a rule enforced
 * in only one direction leaves two panels side by side on desktop.
 */
function buildApp() {
  const ctx = loadScripts(
    [
      'workspace/chat/ui/static/chat/ui/js/panels.js',
      'workspace/chat/ui/static/chat/ui/js/threads.js',
    ],
    {
      getCSRFToken: () => 'csrf-token',
      document: { querySelectorAll: () => [], getElementById: () => null },
      fetch: async () => ({ ok: true, json: async () => ({}) }),
    },
  );
  // Same composition order as chatApp().
  const app = { ...ctx.chatPanelsMixin(), ...ctx.chatThreadsMixin() };
  Object.assign(app, {
    activeConversation: { uuid: 'c1' },
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

test('closing the search panel leaves the thread alone', () => {
  const app = buildApp();
  app.showSearchPanel = true;
  app.openThreadRoot = 'r1';

  app.toggleSearchPanel();

  assert.equal(app.showSearchPanel, false);
  assert.equal(app.openThreadRoot, 'r1', 'closing a panel must not close another');
});
