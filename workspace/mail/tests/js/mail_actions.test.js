'use strict';

// Message actions run side by side: the busy state lasts until the last one
// finishes, and an AI summary requested for one message never lands on the
// message opened after it.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('../../../common/tests/js/loader');

const MSG_A = '55555555-5555-4555-8555-555555555555';
const MSG_B = '66666666-6666-4666-8666-666666666666';
const TASK = '77777777-7777-4777-8777-777777777777';

// A request the test answers when it chooses to.
function pending(log, url, opts = {}) {
  return new Promise((resolve) => {
    log.push({
      url,
      method: opts.method || 'GET',
      answer: (body = {}, ok = true) => resolve({ ok, json: async () => body }),
    });
  });
}

function makeApp() {
  const fetches = [];
  const actions = [];
  const intervals = [];
  const ctx = loadScripts(
    [
      'workspace/common/static/ui/js/uuid.js',
      'workspace/common/static/ui/js/latest_only.js',
      'workspace/common/static/ui/js/attachment_input.js',
      'workspace/mail/ui/static/mail/ui/js/mail_accounts.js',
      'workspace/mail/ui/static/mail/ui/js/mail_folders.js',
      'workspace/mail/ui/static/mail/ui/js/mail_messages.js',
      'workspace/mail/ui/static/mail/ui/js/mail_compose.js',
      'workspace/mail/ui/static/mail/ui/js/mail_labels.js',
      'workspace/mail/ui/static/mail/ui/js/mail_ai.js',
      'workspace/mail/ui/static/mail/ui/js/mail_rules.js',
      'workspace/mail/ui/static/mail/ui/js/mail_rules_form.js',
      'workspace/mail/ui/static/mail/ui/js/mail.js',
    ],
    {
      URL,
      URLSearchParams,
      location: new URL('http://testserver/mail'),
      navHistory: { push() {}, replace() {}, returnTo() { return false; } },
      document: { getElementById: () => null, addEventListener() {} },
      addEventListener() {},
      sidebarPreference: { initial: () => false, save: () => {} },
      _mailPrefsReady: Promise.resolve(),
      _mailPrefsCache: {},
      getCSRFToken: () => 'token',
      AppDialog: { confirm: async () => true, error() {} },
      fetch: (url, opts) => pending(fetches, url, opts),
      setInterval: (fn) => { intervals.push(fn); return intervals.length; },
      clearInterval: () => {},
    },
  );
  const app = ctx.mailApp();
  Object.assign(app, {
    async loadMessages() {},
    _fetch(url, opts) {
      if ((opts?.method || 'GET') === 'GET') {
        const uuid = url.split('/').pop();
        return Promise.resolve({ ok: true, json: async () => ({ uuid, is_read: true }) });
      }
      return pending(actions, url, opts);
    },
  });
  return { app, fetches, actions };
}

const message = (uuid) => ({ uuid, is_read: false, is_starred: false, labels: [] });
const settle = () => new Promise((resolve) => setImmediate(resolve));

test('the busy state lasts until the last of two actions finishes', async () => {
  const { app, actions } = makeApp();
  const msg = message(MSG_A);
  app.messages = [msg];
  const starring = app.toggleStar(msg);
  const reading = app.toggleRead(msg, true);
  assert.equal(app.actionInProgress, true);
  actions[0].answer();
  await starring;
  assert.equal(app.actionInProgress, true);
  actions[1].answer();
  await reading;
  assert.equal(app.actionInProgress, false);
});

test('a failed action still releases its share of the busy state', async () => {
  const { app, actions } = makeApp();
  const msg = message(MSG_A);
  app.messages = [msg];
  const deleting = app.deleteMessage(msg);
  await settle();
  const starring = app.toggleStar(msg);
  actions[0].answer({}, false);
  await deleting;
  assert.equal(app.actionInProgress, true);
  actions[1].answer({}, false);
  await starring;
  assert.equal(app.actionInProgress, false);
});

test('a summary still polling is not written onto the next message opened', async () => {
  const { app, fetches } = makeApp();
  await app.selectMessage(message(MSG_A));
  const summarizing = app.summarizeMessage(app.messageDetail);
  fetches[0].answer({ uuid: TASK });
  await summarizing;
  assert.equal(fetches[1].url, `/api/v1/ai/tasks/${TASK}`);

  await app.selectMessage(message(MSG_B));
  fetches[1].answer({ status: 'completed', result_html: '<p>About A</p>' });
  await settle();

  assert.equal(app.messageDetail.uuid, MSG_B);
  assert.equal(app.aiSummary, null);
  assert.equal(app.aiSummarizing, false);
});

test('a summary started for a message left before it was queued never polls', async () => {
  const { app, fetches } = makeApp();
  await app.selectMessage(message(MSG_A));
  const summarizing = app.summarizeMessage(app.messageDetail);
  await app.selectMessage(message(MSG_B));
  fetches[0].answer({ uuid: TASK });
  await summarizing;
  await settle();

  assert.equal(fetches.length, 1);
  assert.equal(app.aiSummary, null);
  assert.equal(app.aiSummarizing, false);
});

test('the summary of the message on screen still lands', async () => {
  const { app, fetches } = makeApp();
  await app.selectMessage(message(MSG_A));
  const summarizing = app.summarizeMessage(app.messageDetail);
  fetches[0].answer({ uuid: TASK });
  await summarizing;
  fetches[1].answer({ status: 'completed', result_html: '<p>About A</p>' });
  await settle();

  assert.equal(app.aiSummary, '<p>About A</p>');
  assert.equal(app.aiSummarizing, false);
});
