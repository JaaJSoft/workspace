'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

// flatFolderOptions powers the folder <select>s (apply / move-to-folder) in the
// rules dialog. It must list folders in the same order the sidebar shows them
// (special types first by type order, then nested "other" folders by IMAP
// path) and tag each with `_depth` for indentation - regardless of the
// sidebar's expand/collapse state.
function makeApp() {
  const ctx = loadScript('workspace/mail/ui/static/mail/ui/js/mail_folders.js');
  const app = ctx.mailFoldersMixin();
  app.folders = {
    acc: [
      { uuid: 'i', name: 'INBOX', folder_type: 'inbox', display_name: 'Inbox' },
      { uuid: 's', name: 'Sent', folder_type: 'sent', display_name: 'Sent' },
      { uuid: 'c', name: 'Archive', folder_type: 'archive', display_name: 'Archive' },
      { uuid: 'a', name: 'Work', folder_type: 'other', display_name: 'Work' },
      { uuid: 'b', name: 'Work/Projects', folder_type: 'other', display_name: 'Projects' },
    ],
  };
  app.expandedFolders = {};
  return app;
}

test('flatFolderOptions orders folders like the sidebar with depth tags', () => {
  const app = makeApp();
  const opts = app.flatFolderOptions('acc');

  // Special types first (inbox, sent, archive by type order), then "other"
  // folders with the child nested under its parent. Array.from re-homes the
  // vm-realm arrays so strict deepEqual doesn't trip on the prototype check.
  assert.deepEqual(Array.from(opts.map(f => f.uuid)), ['i', 's', 'c', 'a', 'b']);
  assert.deepEqual(Array.from(opts.map(f => f._depth)), [0, 0, 0, 0, 1]);
});

test('flatFolderOptions ignores the expand/collapse state', () => {
  const app = makeApp();
  // Collapsing the parent must NOT hide the child from a picker.
  app.expandedFolders = { Work: false };
  const opts = app.flatFolderOptions('acc');

  assert.ok(opts.some(f => f.uuid === 'b'), 'collapsed child must still be listed');
});

test('flatFolderOptions returns [] for an unknown account', () => {
  const app = makeApp();
  assert.deepEqual(Array.from(app.flatFolderOptions('nope')), []);
});
