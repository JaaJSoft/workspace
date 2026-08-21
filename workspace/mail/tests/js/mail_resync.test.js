'use strict';

// resync() is what mail runs when the SSE stream comes back up - a resumed
// tab, or a bfcache restore after a mobile back gesture. It has to refresh
// the unread counts in the sidebar as well as the open message list, or the
// user sees a folder tree from before the freeze.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

function makeApp() {
  const ctx = loadScript('workspace/mail/ui/static/mail/ui/js/mail.js', {
    document: { getElementById: () => null },
    attachmentInputMixin: () => ({}),
    mailAccountsMixin: () => ({}),
    mailFoldersMixin: () => ({}),
    mailMessagesMixin: () => ({}),
    mailComposeMixin: () => ({}),
    mailLabelsMixin: () => ({}),
    mailAiMixin: () => ({}),
    mailRulesMixin: () => ({}),
    mailRulesFormMixin: () => ({}),
  });

  const calls = { folders: [], labels: [], messages: 0 };
  const app = ctx.mailApp();
  Object.assign(app, {
    accounts: [{ uuid: 'acc-1' }, { uuid: 'acc-2' }],
    async loadFolders(uuid) { calls.folders.push(uuid); },
    async fetchLabels(uuid) { calls.labels.push(uuid); },
    async loadMessages() { calls.messages++; },
  });
  return { app, calls };
}

test('resync refreshes folders and labels for every account', async () => {
  const { app, calls } = makeApp();

  await app.resync();

  assert.deepEqual(Array.from(calls.folders), ['acc-1', 'acc-2']);
  assert.deepEqual(Array.from(calls.labels), ['acc-1', 'acc-2']);
});

test('resync reloads the open message list', async () => {
  const { app, calls } = makeApp();

  await app.resync();

  assert.equal(calls.messages, 1);
});

test('resync reloads messages only after the folder counts have landed', async () => {
  const { app, calls } = makeApp();
  const order = [];
  app.loadFolders = async () => {
    await Promise.resolve();
    order.push('folders');
  };
  app.loadMessages = async () => { order.push('messages'); };

  await app.resync();

  assert.deepEqual(order, ['folders', 'folders', 'messages']);
  assert.equal(calls.messages, 0);
});

test('resync is a no-op on accounts when the user has none', async () => {
  const { app, calls } = makeApp();
  app.accounts = [];

  await app.resync();

  assert.deepEqual(Array.from(calls.folders), []);
  assert.equal(calls.messages, 1, 'loadMessages self-guards, so calling it is safe');
});
