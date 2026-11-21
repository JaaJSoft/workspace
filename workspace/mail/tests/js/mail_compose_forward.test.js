'use strict';

// Regression test for: forwarding a message dropped its attachments.
//
// Forward opened the composer with the subject and the text only. It now
// carries the original's attachments (minus the pictures its body displays)
// and sends them by uuid on send and on draft save. The server side, where
// those uuids are resolved, is covered by tests/test_forward_attachments.py.

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

class FormDataStub {
  constructor() { this.entries = []; }
  append(key, value) { this.entries.push([key, value]); }
  getAll(key) { return this.entries.filter(([k]) => k === key).map(([, v]) => v); }
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
  _parseEmails: (v) => (Array.isArray(v) ? v : []),
  FormData: FormDataStub,
  AppDialog: {},
  document: { getElementById: () => ({ showModal() {}, close() {} }) },
  window: { mailSignature: {}, clipboardData: null },
});

const DETAIL = {
  uuid: 'msg-1',
  account_id: 'acct-1',
  subject: 'Numbers',
  from_address: { name: 'Alice', email: 'alice@example.com' },
  body_text: 'see attached',
  attachments: [
    { uuid: 'att-pdf', filename: 'report.pdf', size: 1200, embedded: false },
    { uuid: 'att-logo', filename: 'logo.png', size: 300, embedded: true },
  ],
};

// `detail` answers a GET of the message; every other request answers "not
// ok", which keeps sendEmail/_saveDraft on their short error path.
function makeInstance({ detail = DETAIL } = {}) {
  const requests = [];
  const instance = {
    compose: _defaultCompose(),
    accounts: [],
    requests,
    opened: null,
    _fetch(url, options) {
      requests.push({ url, options });
      if (!options && detail && url === `/api/v1/mail/messages/${detail.uuid}`) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(detail) });
      }
      return Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
    },
    formatFullDate: (d) => String(d),
    _refreshDraftsFolderCounts() {},
    appendAttachmentsTo() {},
    clearAttachments() {},
    ...ctx.mailComposeMixin(),
  };
  for (const key of Object.keys(instance)) {
    if (typeof instance[key] === 'function') instance[key] = instance[key].bind(instance);
  }
  instance.showCompose = (defaults) => {
    instance.opened = defaults;
    instance.compose = { ..._defaultCompose(), ...defaults };
  };
  return instance;
}

const uuids = (list) => Array.from(list, (a) => a.uuid);

test('forward carries the original attachments, minus the pictures its body shows', async () => {
  const app = makeInstance();

  await app.forwardMessage(DETAIL);

  assert.deepEqual(uuids(app.opened.mail_attachments), ['att-pdf']);
  assert.equal(app.requests.length, 0, 'an opened message is not fetched again');
});

test('forward from a list row loads the message to get its attachments', async () => {
  const app = makeInstance();
  const row = { uuid: 'msg-1', account_id: 'acct-1', subject: 'Numbers', attachments_count: 2 };

  await app.forwardMessage(row);

  assert.deepEqual(uuids(app.opened.mail_attachments), ['att-pdf']);
  assert.match(app.opened.body, /see attached/);
});

test('send and draft save carry the forwarded attachments that were kept', async () => {
  const app = makeInstance();
  await app.forwardMessage({
    ...DETAIL,
    attachments: [
      { uuid: 'att-a', filename: 'a.pdf', size: 1, embedded: false },
      { uuid: 'att-b', filename: 'b.pdf', size: 1, embedded: false },
    ],
  });
  app.compose.to = ['bob@example.com'];
  app.removeMailAttachment(0);

  await app.sendEmail();
  await app._saveDraft();

  const [send, draft] = app.requests;
  assert.deepEqual(send.options.body.getAll('attachment_uuids'), ['att-b']);
  assert.deepEqual(Array.from(draft.options.body.attachment_uuids), ['att-b']);
});

test('a restored localStorage draft keeps its forwarded attachments', async () => {
  const app = makeInstance();
  await app.forwardMessage(DETAIL);

  app._saveComposeToLocalStorage();

  assert.deepEqual(uuids(app._getLocalStorageDraft().mail_attachments), ['att-pdf']);
});
