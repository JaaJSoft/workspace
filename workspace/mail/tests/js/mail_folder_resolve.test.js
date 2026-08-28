'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('../../../common/tests/js/loader');

// A folder uuid captured before a merge (a bookmark, a push notification, a
// search hit) must still resolve once the folder becomes an alias. Aliases
// never appear as top-level entries in the folder payload - only nested
// under their canonical's `aliases` array - so a bare uuid match misses
// them. `_findFolderById` (mail.js) and `_openMessageById` (mail_messages.js)
// both go through `_resolveFolderUuid` (mail_folders.js) for this.
function makeApp() {
  const ctx = loadScripts(
    [
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
    { document: { getElementById: () => null } },
  );
  const app = ctx.mailApp();
  app.folders = {
    acc1: [
      {
        uuid: 'trash-uuid',
        name: 'Trash',
        folder_type: 'trash',
        display_name: 'Trash',
        aliases: [{ uuid: 'corbeille-uuid', display_name: 'Corbeille' }],
      },
    ],
    acc2: [
      {
        uuid: 'other-uuid',
        name: 'Other',
        folder_type: 'other',
        display_name: 'Other',
        aliases: [],
      },
    ],
  };
  return app;
}

test('_findFolderById resolves an alias uuid to its canonical folder', () => {
  const app = makeApp();
  const found = app._findFolderById('corbeille-uuid');
  assert.ok(found, 'expected the canonical to be returned for an alias uuid');
  assert.equal(found.uuid, 'trash-uuid');
});

test('_findFolderById still resolves a canonical by its own uuid', () => {
  const app = makeApp();
  assert.equal(app._findFolderById('trash-uuid').uuid, 'trash-uuid');
});

test('_findFolderById does not leak a match across accounts', () => {
  const app = makeApp();
  // "corbeille-uuid" only exists as an alias under acc1's canonical; acc2
  // must not resolve it to anything.
  app.folders.acc2[0].aliases = [];
  assert.equal(app._findFolderById('unknown-uuid'), null);
});

test('_findFolderById returns null for an unknown uuid', () => {
  const app = makeApp();
  assert.equal(app._findFolderById('nope'), null);
});

test('_resolveFolderUuid is scoped to the given account', () => {
  const app = makeApp();
  // The alias belongs to acc1's group; looking it up under acc2 must miss.
  assert.equal(app._resolveFolderUuid('acc2', 'corbeille-uuid'), null);
  assert.equal(app._resolveFolderUuid('acc1', 'corbeille-uuid').uuid, 'trash-uuid');
});
