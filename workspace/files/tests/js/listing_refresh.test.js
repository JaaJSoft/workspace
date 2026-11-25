'use strict';

// Every component that changed what the listing shows re-renders it in
// place: the address bar's URL into #folder-browser, through the
// component's own $ajax so its ajax:* listeners see the request.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('../../../common/tests/js/loader');

const PATH = '/files/0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b';
const SEARCH = '?sort=name';
const LISTING = [[PATH + SEARCH, { target: 'folder-browser' }]];

// Photos loads the share modal without folder_nav.js, on a page with no listing.
function loadPage({ listing = true, folderNav = true } = {}) {
  const listeners = {};
  const ctx = loadScripts(
    [
      'workspace/core/static/core/js/user_settings.js',
      ...(folderNav ? ['workspace/files/ui/static/files/ui/js/folder_nav.js'] : []),
      'workspace/files/ui/static/files/ui/js/file_operations.js',
      'workspace/files/ui/static/files/ui/js/file_actions.js',
      'workspace/files/ui/static/files/ui/js/preferences.js',
      'workspace/files/ui/static/files/ui/js/browser.js',
      'workspace/files/ui/static/files/ui/js/table.js',
      'workspace/files/ui/static/files/ui/js/pinned.js',
      'workspace/files/ui/static/files/ui/js/share_modal.js',
      'workspace/files/ui/static/files/ui/js/storage.js',
    ],
    {
      document: {
        getElementById: (id) => (listing && id === 'folder-browser' ? { id, dataset: {} } : null),
        addEventListener: () => {},
        querySelector: () => null,
        querySelectorAll: () => [],
      },
      addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); },
      dispatchEvent: (event) => { (listeners[event.type] || []).forEach((fn) => fn(event)); return true; },
      CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
      Event: class { constructor(type) { this.type = type; } },
      matchMedia: () => ({ matches: false, addEventListener: () => {} }),
      sidebarPreference: { initial: () => false, save: () => {} },
      navHistory: { canGoBack: () => false, canGoForward: () => false },
      location: { pathname: PATH, search: SEARCH },
      URLSearchParams,
      getCSRFToken: () => 'token',
      fetch: async () => ({ ok: true, json: async () => ({}) }),
      setTimeout: (fn) => { fn(); return 1; },
      clearTimeout: () => {},
      AppAlert: { success() {}, error() {} },
      tagsMixin: () => ({}),
      propertiesPanelMixin: () => ({}),
    },
  );
  return ctx;
}

// The options object is built inside the vm: compare it as plain JSON.
function recordAjax(component) {
  const calls = [];
  component.$ajax = (url, options) => {
    calls.push([url, JSON.parse(JSON.stringify(options))]);
    return Promise.resolve();
  };
  return calls;
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

test('the browser refreshes the listing the address bar shows', () => {
  const browser = loadPage().fileBrowser();
  const calls = recordAjax(browser);
  browser.refreshFolderBrowser();
  assert.deepEqual(calls, LISTING);
});

test('the table reloads the listing the address bar shows', () => {
  const table = loadPage().fileTableControls();
  const calls = recordAjax(table);
  table.reloadListing();
  assert.deepEqual(calls, LISTING);
});

test('switching the view mode re-renders the listing once it is saved', async () => {
  const toggle = loadPage().viewToggle();
  toggle.init();
  const calls = recordAjax(toggle);
  await toggle.setViewMode('mosaic');
  assert.deepEqual(calls, LISTING);
});

test('a preference rendered server-side re-renders the listing once it is saved', async () => {
  const popover = loadPage().filePreferences();
  await popover.init();
  const calls = recordAjax(popover);
  popover.update('breadcrumbCollapse', 2);
  await settle();
  assert.deepEqual(calls, LISTING);
});

test('pinning a dropped folder refreshes the listing', async () => {
  const ctx = loadPage();
  ctx.fileActions.setPinned = async (uuids) => uuids;
  const section = ctx.pinnedFoldersSection();
  const calls = recordAjax(section);
  await section.onDrop({
    dataTransfer: { getData: () => JSON.stringify({ uuid: 'folder-uuid' }) },
  });
  assert.deepEqual(calls, LISTING);
});

test('saving the shares refreshes the listing so its shared badge updates', async () => {
  const modal = loadPage().shareModal();
  modal.fileUuid = 'file-uuid';
  modal.pendingAdds = [{ type: 'user', id: 3, permission: 'ro' }];
  modal.loadShares = async () => {};
  modal.closeModal = () => {};
  const calls = recordAjax(modal);
  await modal.save();
  assert.deepEqual(calls, LISTING);
});

test('saving the shares from a page without a listing fetches nothing', async () => {
  const modal = loadPage({ listing: false, folderNav: false }).shareModal();
  modal.fileUuid = 'file-uuid';
  modal.pendingAdds = [{ type: 'user', id: 3, permission: 'ro' }];
  modal.loadShares = async () => {};
  let closed = false;
  modal.closeModal = () => { closed = true; };
  const calls = recordAjax(modal);
  await modal.save();
  assert.deepEqual(calls, []);
  assert.equal(closed, true);
});

test('emptying space from the storage dialog refreshes the listing behind it', () => {
  const dialog = loadPage().storageAnalysis();
  dialog.reload = () => {};
  const calls = recordAjax(dialog);
  dialog.afterMutation();
  assert.deepEqual(calls, LISTING);
});

test('the storage dialog refreshes nothing on a page without a listing', () => {
  const dialog = loadPage({ listing: false }).storageAnalysis();
  dialog.reload = () => {};
  const calls = recordAjax(dialog);
  dialog.afterMutation();
  assert.deepEqual(calls, []);
});
