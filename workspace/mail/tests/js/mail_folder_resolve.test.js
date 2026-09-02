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
    { document: { getElementById: () => null }, sidebarPreference: { initial: () => false, save: () => {} } },
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

// Opening the "Merged folders" dialog for one account and then quickly for
// another leaves two requests in flight. Whichever returns last used to win,
// so the dialog could show account A's groups while mergedGroupsAccount named
// B - and unmergeFolder reloads whichever account is stored there.
function makeDialogApp(responses) {
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
    {
      document: { getElementById: () => ({ showModal() {}, close() {} }) },
      sidebarPreference: { initial: () => false, save: () => {} },
    },
  );
  const app = ctx.mailApp();
  // Each account's response resolves only when its recorded release() runs,
  // so the test decides the completion order.
  app._fetch = (url) => {
    const account = url.match(/account=([^&]+)/)[1];
    return new Promise((resolve) => {
      responses[account] = () =>
        resolve({ ok: true, json: async () => responses.payloads[account] });
    });
  };
  return app;
}

const groupPayload = (name) => [
  {
    uuid: `${name}-canonical`,
    alias_of: null,
    display_name: name,
    aliases: [{ uuid: `${name}-alias`, display_name: `${name} alias` }],
  },
];

test('a superseded merged-folders response does not overwrite the current one', async () => {
  const responses = { payloads: { acc1: groupPayload('acc1'), acc2: groupPayload('acc2') } };
  const app = makeDialogApp(responses);

  const first = app._showMergedFolders({ uuid: 'acc1' });
  const second = app._showMergedFolders({ uuid: 'acc2' });

  // The second open wins, then the first request finally lands.
  responses.acc2();
  await second;
  responses.acc1();
  await first;

  assert.equal(app.mergedGroupsAccount, 'acc2');
  assert.equal(app.mergedGroups[0].canonical.uuid, 'acc2-canonical');
});
