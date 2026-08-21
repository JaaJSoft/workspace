'use strict';

// Regression test for: replies were sent without any link to their parent.
//
// replyTo/replyAll already stored `reply_message_id` in the compose state, but
// neither the send FormData nor the draft payload carried it, so the server had
// no way to write In-Reply-To / References and every reply started a new thread.
// These tests pin the wire format of both requests; the header derivation
// itself is covered by the Python tests in tests/test_reply_threading.py.

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function _defaultCompose() {
  return {
    account_id: '', to: [], cc: [], bcc: [],
    subject: '', body: '', is_reply: false, reply_message_id: null,
    attachments: [], picked_files: [], sending: false, error: '',
    draft_id: null, saving: false, last_saved: null,
    _saveTimer: null, _sigBlock: '',
  };
}

function _parseEmails(str) {
  if (Array.isArray(str)) return str.filter(Boolean);
  if (!str || typeof str !== 'string') return [];
  return str.split(/[,;]\s*/).map(s => s.trim()).filter(Boolean);
}

// Minimal FormData: records every append so a test can assert on the payload.
class FormDataStub {
  constructor() { this.entries = []; }
  append(key, value) { this.entries.push([key, value]); }
  get(key) {
    const hit = this.entries.find(([k]) => k === key);
    return hit ? hit[1] : null;
  }
}

const localStorageStub = (() => {
  const store = {};
  return {
    setItem(k, v) { store[k] = String(v); },
    getItem(k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    removeItem(k) { delete store[k]; },
  };
})();

const ctx = loadScript('workspace/mail/ui/static/mail/ui/js/mail_compose.js', {
  localStorage: localStorageStub,
  _defaultCompose,
  _parseEmails,
  FormData: FormDataStub,
  AppDialog: {},
  document: { getElementById: () => ({ showModal() {}, close() {} }) },
  window: { mailSignature: {}, clipboardData: null },
});

const mixin = ctx.mailComposeMixin;

// Builds a component exposing the mixin methods plus a _fetch that records the
// request and answers "not ok", which keeps sendEmail/_saveDraft on their short
// error path instead of touching the rest of the app.
function makeInstance() {
  const requests = [];
  const instance = {
    compose: _defaultCompose(),
    accounts: [],
    showCcBcc: false,
    requests,
    _fetch(url, options) {
      requests.push({ url, options });
      return Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
    },
    _refreshDraftsFolderCounts() {},
    // From attachmentInputMixin, spread into mailApp's root in production.
    appendAttachmentsTo() {},
    clearAttachments() {},
    ...mixin(),
  };
  for (const key of Object.keys(instance)) {
    if (typeof instance[key] === 'function') {
      instance[key] = instance[key].bind(instance);
    }
  }
  return instance;
}

test('sendEmail forwards reply_message_id so the server can thread the reply', async () => {
  const app = makeInstance();
  app.compose.account_id = 'acct-1';
  app.compose.to = ['bob@example.com'];
  app.compose.subject = 'Re: Hello';
  app.compose.reply_message_id = 'parent-uuid';

  await app.sendEmail();

  const body = app.requests[0].options.body;
  assert.equal(
    body.get('reply_message_id'),
    'parent-uuid',
    'the parent uuid is in compose state but never reached the send request',
  );
});

test('sendEmail omits reply_message_id on a fresh compose', async () => {
  const app = makeInstance();
  app.compose.account_id = 'acct-1';
  app.compose.to = ['bob@example.com'];
  app.compose.subject = 'Hello';

  await app.sendEmail();

  assert.equal(app.requests[0].options.body.get('reply_message_id'), null);
});

test('_saveDraft forwards reply_message_id', async () => {
  const app = makeInstance();
  app.compose.account_id = 'acct-1';
  app.compose.to = ['bob@example.com'];
  app.compose.subject = 'Re: Hello';
  app.compose.reply_message_id = 'parent-uuid';

  await app._saveDraft();

  assert.equal(app.requests[0].options.body.reply_message_id, 'parent-uuid');
});

test('a restored localStorage draft keeps its parent message', () => {
  const app = makeInstance();
  app.compose.to = ['bob@example.com'];
  app.compose.subject = 'Re: Hello';
  app.compose.reply_message_id = 'parent-uuid';

  app._saveComposeToLocalStorage();

  assert.equal(app._getLocalStorageDraft().reply_message_id, 'parent-uuid');
});
