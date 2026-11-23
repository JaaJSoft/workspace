'use strict';

// An account the server refuses answers 400 with one sentence per field. The
// two account forms show that sentence, not the JSON it came in.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

const REFUSAL = "'127.0.0.1' points to an address this server will not contact.";

function makeApp(body) {
  const ctx = loadScript('workspace/mail/ui/static/mail/ui/js/mail_accounts.js', {});
  const app = ctx.mailAccountsMixin();
  Object.assign(app, {
    accounts: [],
    newAccount: { email: 'me@example.org', imap_host: '127.0.0.1' },
    editAccount: { uuid: 'acc-1', smtp_host: '127.0.0.1' },
    async _fetch() {
      return { status: 400, ok: false, json: async () => body };
    },
  });
  return app;
}

test('adding an account shows the sentence of the refused field', async () => {
  const app = makeApp({ imap_host: [REFUSAL] });
  await app.addAccount();
  assert.equal(app.accountError, REFUSAL);
});

test('saving an account shows the sentence of the refused field', async () => {
  const app = makeApp({ smtp_host: [REFUSAL] });
  await app.saveAccount();
  assert.equal(app.editAccountError, REFUSAL);
});

test('an answer that carries no sentence falls back to the generic one', async () => {
  const app = makeApp({});
  await app.addAccount();
  assert.equal(app.accountError, 'Failed to add account');
});
