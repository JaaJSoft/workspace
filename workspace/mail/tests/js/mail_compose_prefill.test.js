'use strict';

// What Reply, Reply all and Forward put in the composer.
//
// The composer used to build them itself, from whatever object was at hand:
// from the list's context menu that is a list row, with no Cc and only a
// 300-character snippet, so Reply all dropped every Cc recipient and the
// quote carried the snippet. The server builds them now (services/compose.py,
// covered by tests/test_compose.py and tests/test_compose_prefill_view.py);
// these tests pin that every entry point asks it by uuid and opens what it
// answers, untouched.

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

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

const alerts = [];

const ctx = loadScripts([
  'workspace/mail/ui/static/mail/ui/js/mail_signature.js',
  'workspace/mail/ui/static/mail/ui/js/mail_compose.js',
], {
  localStorage: localStorageStub,
  _defaultCompose,
  _parseEmails: (v) => (Array.isArray(v) ? v.filter(Boolean) : []),
  FormData: FormDataStub,
  AppDialog: {},
  AppAlert: { error: (message) => alerts.push(message) },
  document: { getElementById: () => ({ showModal() {}, close() {} }) },
});

const ROW = { uuid: 'msg-1', account_id: 'acct-1', subject: 'Numbers', snippet: 'see att' };
const DETAIL = { ...ROW, body_text: 'see attached', cc_addresses: [{ email: 'carol@example.com' }] };

const PREFILLS = {
  reply: {
    account_id: 'acct-1',
    to: ['alice@example.com'],
    cc: [],
    subject: 'Re: Numbers',
    body_text: '\n-- \nMe\n\n\n---\nOn Wednesday, Alice wrote:\n> see attached',
    signature_block: '\n-- \nMe\n',
    reply_message_id: 'msg-1',
    attachments: [],
  },
  reply_all: {
    account_id: 'acct-1',
    to: ['alice@example.com', 'bob@example.com'],
    cc: ['carol@example.com'],
    subject: 'Re: Numbers',
    body_text: '\n-- \nMe\n\n\n---\nOn Wednesday, Alice wrote:\n> see attached',
    signature_block: '\n-- \nMe\n',
    reply_message_id: 'msg-1',
    attachments: [],
  },
  forward: {
    account_id: 'acct-1',
    to: [],
    cc: [],
    subject: 'Fwd: Numbers',
    body_text: '\n-- \nMe\n\n\n---\nForwarded message from Alice:\n\nsee attached',
    signature_block: '\n-- \nMe\n',
    reply_message_id: null,
    attachments: [
      { uuid: 'att-a', filename: 'a.pdf', size: 1 },
      { uuid: 'att-b', filename: 'b.pdf', size: 1 },
    ],
  },
};

// The prefill endpoint answers from PREFILLS (or fails with `failPrefill`);
// every other request answers "not ok", which keeps sendEmail/_saveDraft on
// their short error path.
function makeInstance({ failPrefill = false } = {}) {
  const requests = [];
  const instance = {
    compose: _defaultCompose(),
    // The account's signature differs from the one the server put in the
    // body: a composer that signed the body again would show.
    accounts: [{ uuid: 'acct-1', email: 'me@example.com', signature: 'Someone else' }],
    showCcBcc: false,
    requests,
    _fetch(url, options) {
      requests.push({ url, options });
      const match = url.match(/^\/api\/v1\/mail\/messages\/([^/]+)\/compose\?mode=(\w+)$/);
      if (match && !options) {
        if (failPrefill) return Promise.reject(new Error('offline'));
        const body = JSON.stringify(PREFILLS[match[2]]);
        return Promise.resolve({ ok: true, json: () => Promise.resolve(JSON.parse(body)) });
      }
      return Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
    },
    _refreshDraftsFolderCounts() {},
    appendAttachmentsTo() {},
    clearAttachments() {},
    ...ctx.mailComposeMixin(),
  };
  for (const key of Object.keys(instance)) {
    if (typeof instance[key] === 'function') instance[key] = instance[key].bind(instance);
  }
  return instance;
}

const entryPoints = [
  ['reply', 'replyTo'],
  ['reply_all', 'replyAll'],
  ['forward', 'forwardMessage'],
];

for (const [mode, method] of entryPoints) {
  for (const [origin, msg] of [['the open message', DETAIL], ['a list row', ROW]]) {
    test(`${method} from ${origin} opens what the server built`, async () => {
      const app = makeInstance();

      await app[method](msg);

      assert.deepEqual(app.requests.map((r) => r.url), [`/api/v1/mail/messages/msg-1/compose?mode=${mode}`]);
      const expected = PREFILLS[mode];
      assert.deepEqual(Array.from(app.compose.to), expected.to);
      assert.deepEqual(Array.from(app.compose.cc), expected.cc);
      assert.equal(app.compose.subject, expected.subject);
      assert.equal(app.compose.body, expected.body_text, 'the body is not signed a second time');
      assert.equal(app.compose._sigBlock, expected.signature_block);
      assert.equal(app.compose.account_id, expected.account_id);
      assert.equal(app.compose.reply_message_id, expected.reply_message_id);
      assert.deepEqual(Array.from(app.compose.mail_attachments, (a) => a.uuid), expected.attachments.map((a) => a.uuid));
    });
  }
}

test('reply all from a list row keeps the Cc recipients and shows the Cc field', async () => {
  const app = makeInstance();

  await app.replyAll(ROW);

  assert.deepEqual(Array.from(app.compose.cc), ['carol@example.com']);
  assert.equal(app.showCcBcc, true);
});

test('switching account swaps the signature the server put in', async () => {
  const app = makeInstance();
  await app.replyTo(DETAIL);

  app.onComposeAccountChange();

  assert.match(app.compose.body, /^\n-- \nSomeone else\n\n\n---\nOn Wednesday/);
});

test('a prefill that cannot be loaded opens nothing and says so', async () => {
  alerts.length = 0;
  const app = makeInstance({ failPrefill: true });

  await app.replyTo(ROW);

  assert.equal(app.compose.subject, '');
  assert.deepEqual(alerts, ['Could not open the message']);
});

test('send and draft save carry the forwarded attachments that were kept', async () => {
  const app = makeInstance();
  await app.forwardMessage(ROW);
  app.compose.to = ['bob@example.com'];
  app.removeMailAttachment(0);
  app.requests.length = 0;

  await app.sendEmail();
  await app._saveDraft();

  const [send, draft] = app.requests;
  assert.deepEqual(send.options.body.getAll('attachment_uuids'), ['att-b']);
  assert.deepEqual(Array.from(draft.options.body.attachment_uuids), ['att-b']);
});

test('a restored localStorage draft keeps its forwarded attachments and parent', async () => {
  const app = makeInstance();
  await app.forwardMessage(ROW);
  app._saveComposeToLocalStorage();
  assert.deepEqual(Array.from(app._getLocalStorageDraft().mail_attachments, (a) => a.uuid), ['att-a', 'att-b']);

  await app.replyTo(ROW);
  app._saveComposeToLocalStorage();
  assert.equal(app._getLocalStorageDraft().reply_message_id, 'msg-1');
});
