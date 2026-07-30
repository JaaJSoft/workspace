'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const CONV_UUID = 'conv-1';

/**
 * Build a component exposing the panels mixin plus stubs for everything
 * openInfoPanel() reaches outside of it (bot mixin loaders, message pins,
 * Alpine's $nextTick). `calls` records what a panel opening triggered.
 */
function buildApp({ isBot = true } = {}) {
  const scrolled = [];
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/panels.js', {
    document: {
      getElementById: (id) => ({ id, scrollIntoView() { scrolled.push(id); } }),
    },
  });

  const calls = { stats: [], pinned: [], media: [], memories: 0, schedules: [] };
  const app = Object.assign(ctx.chatPanelsMixin(), {
    activeConversation: { uuid: CONV_UUID, is_bot_conversation: isBot },
    isBotConversation(conv) { return !!conv?.is_bot_conversation; },
    loadConversationStats(id) { calls.stats.push(id); },
    loadPinnedMessages(id) { calls.pinned.push(id); },
    loadConversationMedia(id) { calls.media.push(id); },
    loadBotMemories() { calls.memories++; },
    loadScheduledMessages(id) { calls.schedules.push(id); },
    $nextTick(fn) { fn(); },
  });
  return { app, calls, scrolled };
}

test('openInfoPanel loads every section of the panel', () => {
  const { app, calls } = buildApp();

  app.openInfoPanel();

  assert.equal(app.showInfoPanel, true);
  assert.deepStrictEqual(Array.from(calls.stats), [CONV_UUID]);
  assert.deepStrictEqual(Array.from(calls.pinned), [CONV_UUID]);
  assert.deepStrictEqual(Array.from(calls.media), [CONV_UUID]);
  assert.equal(calls.memories, 1, 'AI memories must load for a bot conversation');
  assert.deepStrictEqual(Array.from(calls.schedules), [CONV_UUID]);
});

test('openInfoPanel skips the bot-only sections outside a bot conversation', () => {
  const { app, calls } = buildApp({ isBot: false });

  app.openInfoPanel();

  assert.equal(calls.memories, 0);
  assert.deepStrictEqual(Array.from(calls.schedules), []);
  assert.deepStrictEqual(Array.from(calls.stats), [CONV_UUID]);
});

test('openInfoPanel closes the search panel', () => {
  const { app } = buildApp();
  app.showSearchPanel = true;
  app.searchQuery = 'hello';

  app.openInfoPanel();

  assert.equal(app.showSearchPanel, false);
  assert.equal(app.searchQuery, '');
});

test('openInfoPanel scrolls to the requested section once rendered', () => {
  const { app, scrolled } = buildApp();

  app.openInfoPanel({ scrollTo: 'pinned-messages-section' });

  assert.deepStrictEqual(Array.from(scrolled), ['pinned-messages-section']);
});

/**
 * Same component, plus the conversations mixin so the sidebar context-menu
 * action can be driven. Both scripts run in their own vm context; merging
 * the returned plain objects mirrors how index.html spreads the mixins.
 */
function buildAppWithContextMenu() {
  const built = buildApp();
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/conversations.js', {
    document: { getElementById: () => null },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  });
  // Add only what the panels mixin doesn't already provide, so the stubs
  // (activeConversation, loaders) keep winning over the real mixin's data.
  for (const [key, value] of Object.entries(ctx.chatConversationsMixin())) {
    if (!(key in built.app)) built.app[key] = value;
  }
  built.app.ctxMenu = { open: true, uuid: CONV_UUID };
  return built;
}

test('the sidebar context-menu Info action loads every section too', async () => {
  const { app, calls } = buildAppWithContextMenu();

  await app.ctxMenuAction('info');

  assert.equal(app.showInfoPanel, true);
  assert.equal(calls.memories, 1, 'AI memories must load when the panel opens from the context menu');
  assert.deepStrictEqual(Array.from(calls.schedules), [CONV_UUID]);
  assert.deepStrictEqual(Array.from(calls.pinned), [CONV_UUID]);
  assert.deepStrictEqual(Array.from(calls.stats), [CONV_UUID]);
  assert.deepStrictEqual(Array.from(calls.media), [CONV_UUID]);
});

test('toggleInfoPanel opens with the full load and closes without reloading', () => {
  const { app, calls } = buildApp();

  app.toggleInfoPanel();
  assert.equal(app.showInfoPanel, true);
  assert.equal(calls.memories, 1);

  app.toggleInfoPanel();
  assert.equal(app.showInfoPanel, false);
  assert.equal(calls.memories, 1, 'closing the panel must not refetch');
  assert.deepStrictEqual(Array.from(calls.stats), [CONV_UUID]);
});
