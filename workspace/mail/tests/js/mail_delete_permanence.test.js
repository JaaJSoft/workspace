'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('../../../common/tests/js/loader');

// The delete confirmation tells the user whether the message goes to Trash
// or is gone for good. The server expunges only from Trash (or a folder
// merged into it) and on an account without a Trash folder.
function makeApp(folders) {
  const ctx = loadScripts(
    [
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
    { document: { getElementById: () => null }, sidebarPreference: { initial: () => false, save: () => {} } },
  );
  const app = ctx.mailApp();
  app.folders = folders;
  return app;
}

const INBOX = { uuid: 'inbox-uuid', folder_type: 'inbox', aliases: [] };
const TRASH = {
  uuid: 'trash-uuid',
  folder_type: 'trash',
  aliases: [{ uuid: 'corbeille-uuid', display_name: 'Corbeille' }],
};

test('a message outside Trash goes to Trash', () => {
  const app = makeApp({ acc1: [INBOX, TRASH] });
  assert.equal(app._deleteIsPermanent({ account_id: 'acc1', folder_id: 'inbox-uuid' }), false);
});

test('a message in Trash, or in a folder merged into it, is deleted for good', () => {
  const app = makeApp({ acc1: [INBOX, TRASH] });
  assert.equal(app._deleteIsPermanent({ account_id: 'acc1', folder_id: 'trash-uuid' }), true);
  assert.equal(app._deleteIsPermanent({ account_id: 'acc1', folder_id: 'corbeille-uuid' }), true);
});

test('an account without a Trash folder deletes for good', () => {
  const app = makeApp({ acc1: [INBOX] });
  assert.equal(app._deleteIsPermanent({ account_id: 'acc1', folder_id: 'inbox-uuid' }), true);
});

test('an account whose folders are not loaded keeps the Trash wording', () => {
  const app = makeApp({});
  assert.equal(app._deleteIsPermanent({ account_id: 'acc1', folder_id: 'inbox-uuid' }), false);
});
