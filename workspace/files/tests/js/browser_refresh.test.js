'use strict';

// The listing refreshes when something outside it changed the files it
// shows: the image viewer announces a copy it saved, or an edit, with
// `files-changed`.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('../../../common/tests/js/loader');

function makeBrowser() {
  const listeners = {};
  const ctx = loadScripts([
    'workspace/files/ui/static/files/ui/js/file_operations.js',
    'workspace/files/ui/static/files/ui/js/file_actions.js',
    'workspace/files/ui/static/files/ui/js/browser.js',
  ], {
    tagsMixin: () => ({ loadTags() {} }),
    propertiesPanelMixin: () => ({ followPropertiesUrl() {} }),
    document: {
      addEventListener: () => {},
      getElementById: () => null,
      querySelector: () => null,
      querySelectorAll: () => [],
    },
    addEventListener: (type, handler) => { (listeners[type] = listeners[type] || []).push(handler); },
    location: { pathname: '/files', search: '' },
    URLSearchParams,
    fetch: async () => ({ ok: false, json: async () => ({}) }),
  });
  const browser = ctx.fileBrowser();
  browser.$nextTick = (fn) => fn && fn();
  browser.$watch = () => {};
  let refreshes = 0;
  browser.refreshFolderBrowser = () => { refreshes += 1; };
  browser.init();
  const fire = (type) => (listeners[type] || []).forEach((handler) => handler({ detail: {} }));
  return { fire, refreshes: () => refreshes };
}

test('a file changed outside the listing refreshes it', () => {
  const { fire, refreshes } = makeBrowser();
  fire('files-changed');
  assert.equal(refreshes(), 1);
});
