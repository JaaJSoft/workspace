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
    mail_attachments: [], sending: false, error: '',
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

// A send left pending until the test calls `answer(body, ok)`.
function makeSendingInstance() {
  const app = makeInstance();
  let resolve;
  app._fetch = (url, options) => {
    app.requests.push({ url, options });
    return new Promise((r) => {
      resolve = (body, ok = true) => r({ ok, json: () => Promise.resolve(body) });
    });
  };
  app.messages = [
    { uuid: 'parent-a', is_answered: false },
    { uuid: 'parent-b', is_answered: false },
  ];
  app.messageDetail = app.messages[1];
  app.compose.account_id = 'acct-1';
  app.compose.to = ['bob@example.com'];
  app.compose.reply_message_id = 'parent-a';
  return { app, answer: (body, ok) => resolve(body, ok) };
}

// Discarded while the send was pending, then a new message started.
function replaceComposer(app) {
  app.compose = {
    ..._defaultCompose(),
    to: ['carol@example.com'],
    subject: 'Another message',
    body: 'half written',
  };
  return app.compose;
}

test('a send that lands leaves alone the composer opened meanwhile', async () => {
  const { app, answer } = makeSendingInstance();
  const sentComposer = app.compose;
  sentComposer.draft_id = 'draft-of-the-sent-message';
  const deleted = [];
  app._deleteDraft = (id) => deleted.push(id);

  const sending = app.sendEmail();
  const next = replaceComposer(app);
  answer({ status: 'sent', answered_message_id: null });
  await sending;

  assert.equal(app.compose, next, 'the composer opened during the send was reset');
  assert.deepEqual(
    [next.subject, next.body, Array.from(next.to)],
    ['Another message', 'half written', ['carol@example.com']],
  );
  assert.deepEqual(deleted, ['draft-of-the-sent-message']);
  assert.equal(sentComposer.sending, false);
});

test('a send that fails reports on its own composer, not the one opened meanwhile', async () => {
  const { app, answer } = makeSendingInstance();
  const sentComposer = app.compose;

  const sending = app.sendEmail();
  const next = replaceComposer(app);
  answer({ detail: 'Account not found' }, false);
  await sending;

  assert.equal(next.error, '');
  assert.equal(sentComposer.error, 'Account not found');
});

test('the message marked answered is the one the server names', async () => {
  const { app, answer } = makeSendingInstance();

  const sending = app.sendEmail();
  // Closed and reopened on another message while the send was pending.
  app.compose = { ..._defaultCompose(), reply_message_id: 'parent-b' };
  answer({ status: 'sent', answered_message_id: 'parent-a' });
  await sending;

  assert.equal(app.messages[0].is_answered, true);
  assert.equal(
    app.messages[1].is_answered,
    false,
    'the reply in flight answered parent-a, not the message the dialog moved on to',
  );
});

test('a parent the server did not mark stays unanswered', async () => {
  // Sent from another account than the parent's: the server threads nothing
  // and marks nothing.
  const { app, answer } = makeSendingInstance();

  const sending = app.sendEmail();
  answer({ status: 'sent', answered_message_id: null });
  await sending;

  assert.deepEqual(app.messages.map(m => m.is_answered), [false, false]);
});
