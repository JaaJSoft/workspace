'use strict';

// The sidebar accounts menu: one row per account opening its settings, then
// "Add account". Every action closes the menu first.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

function component() {
  const ctx = loadScript('workspace/mail/ui/static/mail/ui/js/mail_accounts.js');
  const calls = [];
  const app = Object.assign(ctx.mailAccountsMixin(), {
    accountsMenu: { open: true, x: 0, y: 0 },
    showAddAccount: () => calls.push('add'),
    showEditAccount: (account) => calls.push(`edit:${account.uuid}`),
  });
  return { app, calls };
}

test('add closes the menu and opens the add-account dialog', () => {
  const { app, calls } = component();
  app.accountsMenuAction('add');
  assert.equal(app.accountsMenu.open, false);
  assert.deepEqual(calls, ['add']);
});

test('edit closes the menu and opens the settings of that account', () => {
  const { app, calls } = component();
  app.accountsMenuAction('edit', { uuid: 'a1' });
  assert.equal(app.accountsMenu.open, false);
  assert.deepEqual(calls, ['edit:a1']);
});

test('edit without an account only closes the menu', () => {
  const { app, calls } = component();
  app.accountsMenuAction('edit');
  assert.equal(app.accountsMenu.open, false);
  assert.deepEqual(calls, []);
});
