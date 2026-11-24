'use strict';

// The compose dialog's trash button discards: nothing is saved, and a draft an
// autosave already stored on the server is deleted - even when that autosave
// is still waiting for its answer when the user discards.

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function _defaultCompose() {
  return {
    account_id: '', to: [], cc: [], bcc: [],
    subject: '', body: '', is_reply: false, reply_message_id: null,
    mail_attachments: [], sending: false, error: '',
    draft_id: null, saving: false, last_saved: null,
    _saveTimer: null, _sigBlock: '',
  };
}

function makeLocalStorage() {
  const store = {};
  return {
    store,
    setItem(k, v) { store[k] = String(v); },
    getItem(k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    removeItem(k) { delete store[k]; },
  };
}

function setup() {
  const localStorage = makeLocalStorage();
  const ctx = loadScript('workspace/mail/ui/static/mail/ui/js/mail_compose.js', {
    localStorage,
    _defaultCompose,
    _parseEmails: (v) => (Array.isArray(v) ? v : []),
    AppDialog: {},
    document: { getElementById: () => ({ showModal() {}, close() {} }) },
    window: { mailSignature: {} },
  });

  const requests = [];
  // Draft saves answer only when the test releases them, so a discard can
  // land while one is in flight.
  const pendingSaves = [];
  const instance = {
    compose: _defaultCompose(),
    accounts: [],
    requests,
    pendingSaves,
    _fetch(url, options = {}) {
      requests.push({ url, method: options.method || 'GET' });
      if (url === '/api/v1/mail/drafts' && options.method === 'POST') {
        return new Promise((resolve) => {
          pendingSaves.push((uuid) => resolve({ ok: true, json: () => Promise.resolve({ uuid }) }));
        });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    },
    clearAttachments() {},
    ...ctx.mailComposeMixin(),
    refreshedAccounts: [],
  };
  instance._refreshDraftsFolderCounts = (accountId) => { instance.refreshedAccounts.push(accountId); };
  for (const key of Object.keys(instance)) {
    if (typeof instance[key] === 'function') instance[key] = instance[key].bind(instance);
  }
  return { instance, localStorage };
}

function fillCompose(instance) {
  instance.compose.account_id = 'acc-1';
  instance.compose.to = ['bob@example.com'];
  instance.compose.subject = 'Hello';
  instance.compose.body = 'Draft body';
}

const posts = (requests) => requests.filter((r) => r.method === 'POST');
const deletes = (requests) => requests.filter((r) => r.method === 'DELETE').map((r) => r.url);

test('discarding saves nothing and deletes the draft an autosave stored', async () => {
  const { instance, localStorage } = setup();
  fillCompose(instance);
  instance.compose.draft_id = 'draft-1';
  localStorage.setItem('mail_compose_draft', '{}');

  await instance.discardCompose();

  assert.equal(posts(instance.requests).length, 0);
  assert.deepEqual(deletes(instance.requests), ['/api/v1/mail/drafts/draft-1']);
  assert.equal(localStorage.getItem('mail_compose_draft'), null);
  assert.equal(instance.compose.subject, '');
  assert.deepEqual(instance.refreshedAccounts, ['acc-1']);
});

test('discarding a compose never saved sends nothing', async () => {
  const { instance } = setup();
  fillCompose(instance);

  await instance.discardCompose();

  assert.deepEqual(instance.requests, []);
});

test('a draft stored by an autosave still in flight is deleted too', async () => {
  const { instance, localStorage } = setup();
  fillCompose(instance);
  const saving = instance._saveDraft();

  const discarding = instance.discardCompose();
  instance.pendingSaves[0]('draft-late');
  await saving;
  await discarding;

  assert.deepEqual(deletes(instance.requests), ['/api/v1/mail/drafts/draft-late']);
  assert.equal(localStorage.getItem('mail_compose_draft'), null);
  assert.equal(instance.compose.draft_id, null, 'the next compose must not inherit the id');
  assert.ok(instance.refreshedAccounts.length > 0);
  assert.ok(instance.refreshedAccounts.every((id) => id === 'acc-1'));
});
