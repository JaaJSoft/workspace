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
      'workspace/mail/ui/static/mail/ui/js/mail_body_frame.js',
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

// "Confirm before delete" turned off skips the dialog for a move to Trash,
// which can be undone, never for a delete that cannot.
function makeDeletingApp(folders, prefs) {
  const confirms = [];
  const ctx = loadScripts(
    [
      'workspace/common/static/ui/js/latest_only.js',
      'workspace/common/static/ui/js/attachment_input.js',
      'workspace/mail/ui/static/mail/ui/js/mail_accounts.js',
      'workspace/mail/ui/static/mail/ui/js/mail_folders.js',
      'workspace/mail/ui/static/mail/ui/js/mail_messages.js',
      'workspace/mail/ui/static/mail/ui/js/mail_body_frame.js',
      'workspace/mail/ui/static/mail/ui/js/mail_compose.js',
      'workspace/mail/ui/static/mail/ui/js/mail_labels.js',
      'workspace/mail/ui/static/mail/ui/js/mail_ai.js',
      'workspace/mail/ui/static/mail/ui/js/mail_rules.js',
      'workspace/mail/ui/static/mail/ui/js/mail_rules_form.js',
      'workspace/mail/ui/static/mail/ui/js/mail.js',
    ],
    {
      document: { getElementById: () => null },
      sidebarPreference: { initial: () => false, save: () => {} },
      _mailPrefsCache: prefs,
      gettext: (text) => text,
      AppDialog: { confirm: async (options) => { confirms.push(options); return true; } },
    },
  );
  const app = ctx.mailApp();
  app.folders = folders;
  const deleted = [];
  app._fetch = async (url) => { deleted.push(url); return { ok: true }; };
  return { app, confirms, deleted };
}

const IN_INBOX = { uuid: 'm1', account_id: 'acc1', folder_id: 'inbox-uuid' };
const IN_TRASH = { uuid: 'm1', account_id: 'acc1', folder_id: 'trash-uuid' };

test('a move to Trash asks first by default', async () => {
  const { app, confirms, deleted } = makeDeletingApp({ acc1: [INBOX, TRASH] }, {});
  await app.deleteMessage(IN_INBOX);
  assert.equal(confirms.length, 1);
  assert.equal(deleted.length, 1);
});

test('a move to Trash goes straight through when confirming is turned off', async () => {
  const { app, confirms, deleted } = makeDeletingApp(
    { acc1: [INBOX, TRASH] },
    { confirmBeforeDelete: false },
  );
  await app.deleteMessage(IN_INBOX);
  assert.equal(confirms.length, 0);
  assert.equal(deleted.length, 1);
});

test('a permanent delete still asks when confirming is turned off', async () => {
  const { app, confirms } = makeDeletingApp(
    { acc1: [INBOX, TRASH] },
    { confirmBeforeDelete: false },
  );
  await app.deleteMessage(IN_TRASH);
  assert.equal(confirms.length, 1);
});
