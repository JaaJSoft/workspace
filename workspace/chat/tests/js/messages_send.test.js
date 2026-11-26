'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

/**
 * Build a chatMessagesMixin() object with all of sendMessage()'s collaborators
 * stubbed, so the test exercises only the send-success control flow.
 *
 * @param {object} opts
 * @param {boolean} opts.ok - whether the simulated POST succeeds
 * @returns {{ app: object, counters: { refreshed: number, removed: number } }}
 */
function buildApp({ ok, duringPost = () => {} }) {
  let app;
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/messages.js', {
    getCSRFToken: () => 'csrf-token',
    fetch: async () => {
      duringPost(app);
      return {
        ok,
        json: async () => ({ uuid: 'm1', created_at: '2026-01-01T00:00:00Z' }),
      };
    },
  });

  const counters = { refreshed: 0, removed: 0 };
  const refreshedWith = [];
  app = ctx.chatMessagesMixin();
  Object.assign(app, {
    messageBody: 'hello',
    pendingFiles: [],
    pendingPickedFiles: [],
    replyingTo: null,
    activeConversation: { uuid: 'c1' },
    botTyping: false,
    _lastTypingSent: 0,
    _clearDraft() {},
    cancelReply() {},
    _injectOptimisticMessage() {},
    _removeOptimisticMessage() { counters.removed++; },
    isBotConversation() { return false; },
    $nextTick() {},
    scrollToBottom() {},
    async _refreshCurrentMessages() {
      counters.refreshedAway = this.hasNewerMessages;
    },
    refreshConversationItems(uuids) {
      counters.refreshed++;
      refreshedWith.push(...uuids);
    },
  });
  return { app, counters, refreshedWith };
}

test('a successful send refreshes the conversation sidebar row so it bubbles to the top', async () => {
  const { app, counters, refreshedWith } = buildApp({ ok: true });
  await app.sendMessage();
  assert.equal(counters.refreshed, 1, 'sidebar row should refresh exactly once after a successful send');
  assert.deepStrictEqual(refreshedWith, ['c1'], 'the active conversation row should be the one refreshed');
});

test('a failed send does not refresh the conversation sidebar row', async () => {
  const { app, counters } = buildApp({ ok: false });
  await app.sendMessage();
  assert.equal(counters.refreshed, 0, 'sidebar should not refresh when the send fails');
  assert.equal(counters.removed, 1, 'the optimistic message should be rolled back on failure');
});

test('a message sent from the past is refreshed at the present', async () => {
  // A newer page landing while the POST is out sets the flag again; the
  // refresh that shows the sent message must still load the last page.
  const { app, counters } = buildApp({
    ok: true,
    duringPost: (a) => { a.hasNewerMessages = true; },
  });
  app.hasNewerMessages = true;
  await app.sendMessage();
  assert.equal(counters.refreshedAway, false);
});

test('a failed send from the past keeps the past on screen as it was', async () => {
  const { app } = buildApp({ ok: false });
  app.hasNewerMessages = true;
  await app.sendMessage();
  assert.equal(app.hasNewerMessages, true, 'the window is still cut off from the present');
});
