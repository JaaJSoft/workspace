'use strict';

// The sync endpoint only queues the work (202 + the account's updated_at at
// that moment). syncAccount() keeps the spinner up until the account row moves
// past that timestamp, and only then reloads folders and messages.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

const QUEUED_AT = '2026-09-27T10:00:00.000000Z';
const DONE_AT = '2026-09-27T10:00:05.123456Z';

function jsonResponse(status, body) {
  return { status, ok: status >= 200 && status < 300, json: async () => body };
}

function makeApp(accountSnapshots, syncResponse = jsonResponse(202, { status: 'queued', updated_at: QUEUED_AT })) {
  const calls = { polls: 0, folders: 0, messages: 0, spinnerDuringReload: null, dialogs: [] };
  const ctx = loadScript('workspace/mail/ui/static/mail/ui/js/mail_accounts.js', {
    AppDialog: { async message(opts) { calls.dialogs.push(opts); } },
  });
  const app = ctx.mailAccountsMixin();
  Object.assign(app, {
    accounts: [{ uuid: 'acc-1', updated_at: QUEUED_AT, expanded: true }],
    syncingAccounts: {},
    selectedFolder: { account_id: 'acc-1' },
    async _syncPollDelay() {},
    async _fetch(url, opts = {}) {
      if (opts.method === 'POST') return syncResponse;
      const snapshot = accountSnapshots[Math.min(calls.polls, accountSnapshots.length - 1)];
      calls.polls++;
      return snapshot instanceof Error ? Promise.reject(snapshot) : jsonResponse(200, snapshot);
    },
    async loadFolders() {
      calls.folders++;
      calls.spinnerDuringReload = this.syncingAccounts['acc-1'];
    },
    async loadMessages() { calls.messages++; },
  });
  return { app, calls };
}

test('syncAccount polls until updated_at moves, then reloads', async () => {
  const { app, calls } = makeApp([
    { uuid: 'acc-1', updated_at: QUEUED_AT },
    { uuid: 'acc-1', updated_at: QUEUED_AT },
    { uuid: 'acc-1', updated_at: DONE_AT, last_sync_error: '' },
  ]);

  await app.syncAccount('acc-1');

  assert.equal(calls.polls, 3);
  assert.equal(calls.folders, 1);
  assert.equal(calls.messages, 1);
  assert.equal(calls.spinnerDuringReload, true);
  assert.equal(app.syncingAccounts['acc-1'], false);
});

test('the finished account replaces the stale one, keeping UI fields', async () => {
  const { app } = makeApp([{ uuid: 'acc-1', updated_at: DONE_AT, last_sync_error: 'Some folders failed to sync.' }]);

  await app.syncAccount('acc-1');

  assert.equal(app.accounts[0].last_sync_error, 'Some folders failed to sync.');
  assert.equal(app.accounts[0].expanded, true);
});

test('a network error while polling does not end the wait', async () => {
  const { app, calls } = makeApp([
    new Error('offline'),
    { uuid: 'acc-1', updated_at: DONE_AT },
  ]);

  await app.syncAccount('acc-1');

  assert.equal(calls.polls, 2);
  assert.equal(calls.folders, 1);
});

test('a sync that never lands gives up instead of spinning forever', async () => {
  const { app, calls } = makeApp([{ uuid: 'acc-1', updated_at: QUEUED_AT }]);

  await app.syncAccount('acc-1');

  assert.ok(calls.polls > 1);
  assert.equal(calls.folders, 1);
  assert.equal(app.syncingAccounts['acc-1'], false);
});

test('a refused sync shows the server detail and reloads nothing', async () => {
  const { app, calls } = makeApp([], jsonResponse(409, { detail: 'Account is inactive' }));

  await app.syncAccount('acc-1');

  assert.equal(calls.dialogs.length, 1);
  assert.equal(calls.dialogs[0].message, 'Account is inactive');
  assert.equal(calls.polls, 0);
  assert.equal(calls.folders, 0);
  assert.equal(app.syncingAccounts['acc-1'], false);
});

test('a sync that could not be queued shows the error field', async () => {
  const { app, calls } = makeApp([], jsonResponse(503, { status: 'error', error: 'Sync could not be queued' }));

  await app.syncAccount('acc-1');

  assert.equal(calls.dialogs[0].message, 'Sync could not be queued');
  assert.equal(calls.folders, 0);
});
