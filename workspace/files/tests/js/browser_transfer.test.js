'use strict';

// Moving and copying items - the paste of a cut/copied clipboard and the
// drop of a dragged selection share one transfer routine, which hands the
// whole selection to one batch operation. These tests pin the operation it
// asks for and the summary it announces.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('../../../common/tests/js/loader');

const OPERATIONS = '/api/v1/files/operations';

// What the server reports once the operation is over.
function finished(kind, counts, errors = []) {
  return {
    uuid: 'op-1', kind, status: 'completed', target: null, target_name: '',
    total: 1, processed: 1, bytes_total: null, bytes_done: 0, counts, errors,
    cancel_requested: false,
  };
}

function makeBrowser({ currentFolder = '', clipboard = {}, report }) {
  const alerts = [];
  const requests = [];
  const events = [];
  const ctx = loadScripts([
    'workspace/files/ui/static/files/ui/js/file_operations.js',
    'workspace/files/ui/static/files/ui/js/file_actions.js',
    'workspace/files/ui/static/files/ui/js/browser.js',
  ], {
    tagsMixin: () => ({ toggleFileTag: async () => {} }),
    propertiesPanelMixin: () => ({}),
    document: {
      addEventListener: () => {},
      getElementById: (id) =>
        id === 'folder-browser' ? { dataset: { folder: currentFolder } } : null,
      querySelector: () => null,
    },
    CSS: { escape: String },
    location: { pathname: '/files', search: '' },
    getCSRFToken: () => 'token',
    // Collisions are resolved without asking, so no sibling listing is fetched.
    getFilePrefs: () => ({ nameCollision: 'keep_both' }),
    addEventListener: () => {},
    dispatchEvent: (event) => { events.push(event.type); return true; },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
  });
  const store = ctx.createFileOperations({
    request: async (url, { method, body }) => {
      requests.push([method, url, body === undefined ? null : JSON.parse(JSON.stringify(body))]);
      return method === 'POST' ? { ok: true, status: 202, body: report } : { ok: true, status: 204, body: null };
    },
    notify: (type) => events.push(type),
  });
  ctx.Alpine = { store: () => store };
  ctx.AppAlert = {
    info: (m) => alerts.push(['info', m]),
    success: (m) => alerts.push(['success', m]),
    warning: (m) => alerts.push(['warning', m]),
    error: (m) => alerts.push(['error', m]),
  };
  ctx.fileClipboard = {
    cut: () => {}, copy: () => {}, clear: () => { events.push('clipboard-cleared'); },
    getItems: () => [], isCopy: () => false, ...clipboard,
  };
  const browser = ctx.fileBrowser();
  const posted = () => requests.filter(([method]) => method === 'POST').map(([, url, body]) => [url, body]);
  return { browser, alerts, posted, events };
}

test('pasting cut items moves them all in one operation into the current folder', async () => {
  const items = [
    { uuid: 'a', name: 'report.pdf', nodeType: 'file', sourceFolder: 'src' },
    { uuid: 'b', name: 'Archive', nodeType: 'folder', sourceFolder: 'src' },
  ];
  const { browser, alerts, posted, events } = makeBrowser({
    currentFolder: 'target',
    clipboard: { getItems: () => items, isCopy: () => false },
    report: finished('move', { done: 2 }),
  });

  await browser.pasteFromClipboard();

  assert.deepEqual(posted(), [[OPERATIONS, {
    kind: 'move',
    items: [{ uuid: 'a', on_conflict: 'rename' }, { uuid: 'b' }],
    target: 'target',
  }]]);
  assert.deepEqual(Array.from(alerts), [['success', 'Moved 2 items']]);
  assert.ok(events.includes('clipboard-cleared'), 'a cut clipboard is spent by the paste');
  assert.ok(events.includes('file-operation-finished'), 'the listing hears about the end');
});

test('pasting copied items copies them and keeps the clipboard', async () => {
  const items = [{ uuid: 'a', name: 'report.pdf', nodeType: 'file', sourceFolder: 'target' }];
  const { browser, alerts, posted, events } = makeBrowser({
    currentFolder: 'target',
    clipboard: { getItems: () => items, isCopy: () => true },
    report: finished('copy', { done: 1 }),
  });

  await browser.pasteFromClipboard();

  assert.deepEqual(posted(), [[OPERATIONS, { kind: 'copy', items: [{ uuid: 'a' }], target: 'target' }]]);
  assert.deepEqual(Array.from(alerts), [['success', 'Copied 1 item']]);
  assert.ok(!events.includes('clipboard-cleared'), 'a copied clipboard can be pasted again');
});

test('an item the server turned down surfaces its reason', async () => {
  const items = [{ uuid: 'a', name: 'Archive', nodeType: 'folder', sourceFolder: 'src' }];
  const { browser, alerts } = makeBrowser({
    currentFolder: 'target',
    clipboard: { getItems: () => items, isCopy: () => false },
    report: finished('move', { failed: 1 }, [
      { uuid: 'a', name: 'Archive', reason: 'into_itself', message: 'A folder cannot go inside itself.' },
    ]),
  });

  await browser.pasteFromClipboard();

  assert.deepEqual(Array.from(alerts), [['warning', '1 failed - A folder cannot go inside itself.']]);
});

test('a drop moves the dragged items into the target folder and clears the selection', async () => {
  const items = [
    { uuid: 'a', name: 'report.pdf', nodeType: 'file', sourceFolder: 'src' },
    { uuid: 'b', name: 'Archive', nodeType: 'folder', sourceFolder: 'src' },
  ];
  const { browser, alerts, posted, events } = makeBrowser({
    currentFolder: 'src',
    report: finished('move', { done: 2 }),
  });

  await browser.moveItemsTo(items, 'dest');

  assert.deepEqual(posted(), [[OPERATIONS, {
    kind: 'move',
    items: [{ uuid: 'a', on_conflict: 'rename' }, { uuid: 'b' }],
    target: 'dest',
  }]]);
  assert.deepEqual(Array.from(alerts), [['success', 'Moved 2 items']]);
  assert.ok(events.includes('clear-file-selection'));
  assert.ok(!events.includes('clipboard-cleared'), 'a drop never touches the clipboard');
});

test('dropping onto the root names the root as the destination', async () => {
  const items = [{ uuid: 'b', name: 'Archive', nodeType: 'folder', sourceFolder: 'src' }];
  const { browser, posted } = makeBrowser({ currentFolder: 'src', report: finished('move', { done: 1 }) });

  await browser.moveItemsTo(items, null);

  assert.deepEqual(posted(), [[OPERATIONS, { kind: 'move', items: [{ uuid: 'b' }], target: null }]]);
});

test('a dropped file whose origin is unknown is still checked against the root', async () => {
  // Items dragged from a listing that is not a folder carry no sourceFolder:
  // the paste routine must not mistake that for "already at the root".
  const items = [{ uuid: 'a', name: 'report.pdf', nodeType: 'file' }];
  const { browser, posted } = makeBrowser({ currentFolder: '', report: finished('move', { done: 1 }) });

  await browser.moveItemsTo(items, null);

  assert.deepEqual(posted(), [[OPERATIONS, {
    kind: 'move', items: [{ uuid: 'a', on_conflict: 'rename' }], target: null,
  }]]);
});

test('a bulk trash is one operation and announces what went', async () => {
  const { browser, alerts, posted, events } = makeBrowser({ report: finished('trash', { done: 2 }) });
  browser._getItemsFromUuids = () => [];

  await browser.bulkDeleteItems(['a', 'b']);

  assert.deepEqual(posted(), [[OPERATIONS, { kind: 'trash', items: [{ uuid: 'a' }, { uuid: 'b' }] }]]);
  assert.deepEqual(Array.from(alerts), [['success', 'Deleted 2 items']]);
  assert.ok(events.includes('clear-file-selection'));
});

test('emptying the trash names no items', async () => {
  const { browser, posted } = makeBrowser({ report: finished('empty_trash', { done: 4 }) });

  await browser.emptyTrash();

  assert.deepEqual(posted(), [[OPERATIONS, { kind: 'empty_trash' }]]);
  assert.equal(browser.cleaningTrash, false);
});
