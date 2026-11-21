'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('../../../common/tests/js/loader');

// Archive, Report spam and Not spam are offered where the server will act on
// them (services/triage.py): the menus and the E / ! shortcuts read these.
function makeApp(folders, accounts = [{ uuid: 'acc1' }]) {
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
  app.accounts = accounts;
  return app;
}

const folder = (type, extra = {}) => ({ uuid: `${type}-uuid`, folder_type: type, aliases: [], ...extra });
const INBOX = folder('inbox');
const ARCHIVE = folder('archive');
const SPAM = folder('spam', { aliases: [{ uuid: 'pourriel-uuid' }] });
const TRASH = folder('trash');
const DRAFTS = folder('drafts');
const SENT = folder('sent');
const LABEL = folder('other');
const ALL = [INBOX, ARCHIVE, SPAM, TRASH, DRAFTS, SENT, LABEL];

const msgIn = (f) => ({ uuid: `m-${f.uuid}`, account_id: 'acc1', folder_id: f.uuid });

test('archive is offered out of every folder but Archive, Trash, Spam and Drafts', () => {
  const app = makeApp({ acc1: ALL });
  assert.deepEqual(
    ALL.filter(f => app.canArchive(msgIn(f))).map(f => f.folder_type),
    ['inbox', 'sent', 'other'],
  );
});

test('an account without Archive still archives: the server creates the folder', () => {
  const app = makeApp({ acc1: [INBOX, LABEL] });
  assert.equal(app.canArchive(msgIn(INBOX)), true);
  assert.equal(app.canArchive(msgIn(LABEL)), true);
});

test('on Gmail without an Archive folder, only the Inbox has something to archive', () => {
  const app = makeApp({ acc1: [INBOX, LABEL, SENT] }, [{ uuid: 'acc1', archives_in_all_mail: true }]);
  assert.equal(app.canArchive(msgIn(INBOX)), true);
  assert.equal(app.canArchive(msgIn(LABEL)), false);
  assert.equal(app.canArchive(msgIn(SENT)), false);
});

test('report spam needs a Spam folder and a message that is mail received', () => {
  const app = makeApp({ acc1: ALL });
  assert.deepEqual(
    ALL.filter(f => app.canReportSpam(msgIn(f))).map(f => f.folder_type),
    ['inbox', 'archive', 'other'],
  );
  assert.equal(makeApp({ acc1: [INBOX] }).canReportSpam(msgIn(INBOX)), false);
});

test('not spam is offered in Spam, and in a folder merged into it', () => {
  const app = makeApp({ acc1: ALL });
  assert.equal(app.canMarkNotSpam(msgIn(SPAM)), true);
  assert.equal(app.canMarkNotSpam({ account_id: 'acc1', folder_id: 'pourriel-uuid' }), true);
  assert.equal(app.canMarkNotSpam(msgIn(INBOX)), false);
});

test('the batch bar files only the selected messages the action applies to', () => {
  const app = makeApp({ acc1: ALL });
  app.messages = [msgIn(INBOX), msgIn(TRASH), msgIn(LABEL)];
  app.selectedMessages = ['m-inbox-uuid', 'm-trash-uuid'];
  assert.deepEqual(Array.from(app.selectedFileable('canArchive')), ['m-inbox-uuid']);
});
