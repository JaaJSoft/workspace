'use strict';

// Moving and copying items - the paste of a cut/copied clipboard and the
// drop of a dragged selection share one transfer routine. These tests pin
// the requests it sends and the summary it announces.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

function jsonResponse(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function makeBrowser({ currentFolder = '', clipboard = {}, respond } = {}) {
  const alerts = [];
  const requests = [];
  const events = [];
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/browser.js', {
    tagsMixin: () => ({ toggleFileTag: async () => {} }),
    propertiesPanelMixin: () => ({}),
    document: {
      getElementById: (id) =>
        id === 'folder-browser' ? { dataset: { folder: currentFolder } } : null,
      querySelector: () => null,
    },
    location: { pathname: '/files', search: '' },
    getCSRFToken: () => 'token',
    // Collisions are resolved without asking, so no sibling listing is fetched.
    getFilePrefs: () => ({ nameCollision: 'keep_both' }),
    addEventListener: () => {},
    dispatchEvent: (event) => { events.push(event.type); return true; },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
    fetch: async (url, options = {}) => {
      const body = options.body ? JSON.parse(options.body) : null;
      requests.push({ url, method: options.method || 'GET', body });
      return respond(url, options.method || 'GET', body);
    },
  });
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
  browser.$ajax = (url, options) => { events.push(`ajax:${options.target}`); };
  return { browser, alerts, requests, events };
}

test('pasting cut items moves each one into the current folder', async () => {
  const items = [
    { uuid: 'a', name: 'report.pdf', nodeType: 'file', sourceFolder: 'src' },
    { uuid: 'b', name: 'Archive', nodeType: 'folder', sourceFolder: 'src' },
  ];
  const { browser, alerts, requests, events } = makeBrowser({
    currentFolder: 'target',
    clipboard: { getItems: () => items, isCopy: () => false },
    respond: (url) => {
      const uuid = url.split('/').pop();
      return jsonResponse(200, { uuid, name: items.find((i) => i.uuid === uuid).name });
    },
  });

  await browser.pasteFromClipboard();

  assert.deepEqual(Array.from(requests, (r) => [r.method, r.url, { ...r.body }]), [
    ['PATCH', '/api/v1/files/a', { parent: 'target', on_conflict: 'rename' }],
    ['PATCH', '/api/v1/files/b', { parent: 'target' }],
  ]);
  assert.deepEqual(Array.from(alerts), [['success', 'Moved 2 items']]);
  assert.ok(events.includes('clipboard-cleared'), 'a cut clipboard is spent by the paste');
  assert.ok(events.includes('ajax:folder-browser'), 'the listing is refreshed');
});

test('pasting copied items posts a copy and keeps the clipboard', async () => {
  const items = [{ uuid: 'a', name: 'report.pdf', nodeType: 'file', sourceFolder: 'target' }];
  const { browser, alerts, requests, events } = makeBrowser({
    currentFolder: 'target',
    clipboard: { getItems: () => items, isCopy: () => true },
    respond: () => jsonResponse(201, { uuid: 'a2', name: 'report (Copy).pdf' }),
  });

  await browser.pasteFromClipboard();

  assert.deepEqual(Array.from(requests, (r) => [r.method, r.url, { ...r.body }]), [
    ['POST', '/api/v1/files/a/copy', { parent: 'target' }],
  ]);
  assert.deepEqual(Array.from(alerts), [['success', 'Copied 1 item']]);
  assert.ok(!events.includes('clipboard-cleared'), 'a copied clipboard can be pasted again');
});

test('a refused move surfaces the server reason', async () => {
  const items = [{ uuid: 'a', name: 'Archive', nodeType: 'folder', sourceFolder: 'src' }];
  const { browser, alerts } = makeBrowser({
    currentFolder: 'target',
    clipboard: { getItems: () => items, isCopy: () => false },
    respond: () => jsonResponse(400, { parent: ['Cannot move a folder into itself.'] }),
  });

  await browser.pasteFromClipboard();

  assert.deepEqual(Array.from(alerts), [
    ['warning', '1 failed - Cannot move a folder into itself.'],
  ]);
});

test('a drop moves the dragged items into the target folder and clears the selection', async () => {
  const items = [
    { uuid: 'a', name: 'report.pdf', nodeType: 'file', sourceFolder: 'src' },
    { uuid: 'b', name: 'Archive', nodeType: 'folder', sourceFolder: 'src' },
  ];
  const { browser, alerts, requests, events } = makeBrowser({
    currentFolder: 'src',
    respond: (url) => {
      const uuid = url.split('/').pop();
      return jsonResponse(200, { uuid, name: items.find((i) => i.uuid === uuid).name });
    },
  });

  await browser.moveItemsTo(items, 'dest');

  assert.deepEqual(Array.from(requests, (r) => [r.method, r.url, { ...r.body }]), [
    ['PATCH', '/api/v1/files/a', { parent: 'dest', on_conflict: 'rename' }],
    ['PATCH', '/api/v1/files/b', { parent: 'dest' }],
  ]);
  assert.deepEqual(Array.from(alerts), [['success', 'Moved 2 items']]);
  assert.ok(events.includes('clear-file-selection'));
  assert.ok(events.includes('ajax:folder-browser'), 'the listing is refreshed');
  assert.ok(!events.includes('clipboard-cleared'), 'a drop never touches the clipboard');
});

test('dropping onto the root sends a null parent', async () => {
  const items = [{ uuid: 'b', name: 'Archive', nodeType: 'folder', sourceFolder: 'src' }];
  const { browser, requests } = makeBrowser({
    currentFolder: 'src',
    respond: () => jsonResponse(200, { uuid: 'b', name: 'Archive' }),
  });
  await browser.moveItemsTo(items, null);
  assert.deepEqual(Array.from(requests, (r) => [r.method, r.url, { ...r.body }]), [
    ['PATCH', '/api/v1/files/b', { parent: null }],
  ]);
});

test('a dropped file whose origin is unknown is still checked against the root', async () => {
  // Items dragged from a listing that is not a folder carry no sourceFolder:
  // the paste routine must not mistake that for "already at the root".
  const items = [{ uuid: 'a', name: 'report.pdf', nodeType: 'file' }];
  const { browser, requests } = makeBrowser({
    currentFolder: '',
    respond: () => jsonResponse(200, { uuid: 'a', name: 'report.pdf' }),
  });
  await browser.moveItemsTo(items, null);
  assert.deepEqual(Array.from(requests, (r) => [r.method, r.url, { ...r.body }]), [
    ['PATCH', '/api/v1/files/a', { parent: null, on_conflict: 'rename' }],
  ]);
});
