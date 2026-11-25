'use strict';

// The view mode and the tile size are saved from the listing's toolbar
// (viewToggle, table.js), every other preference from the popover
// (filePreferences, preferences.js). Both PUT the whole preferences object,
// so a save from one must carry what the other just changed.

const assert = require('node:assert/strict');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

function loadPage({ reply = { ok: true } } = {}) {
  const listeners = {};
  const saved = [];
  const puts = [];
  const timers = [];
  const ctx = loadScripts(
    [
      'workspace/core/static/core/js/user_settings.js',
      'workspace/files/ui/static/files/ui/js/preferences.js',
      'workspace/files/ui/static/files/ui/js/table.js',
    ],
    {
      document: {
        getElementById: () => null,
        addEventListener: () => {},
      },
      addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); },
      dispatchEvent: (event) => { (listeners[event.type] || []).forEach((fn) => fn(event)); return true; },
      CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
      matchMedia: () => ({ matches: false, addEventListener: () => {} }),
      sidebarPreference: { initial: () => false, save: () => {} },
      location: { pathname: '/files', search: '' },
      getCSRFToken: () => 'token',
      fetch: async (url, options) => {
        saved.push(JSON.parse(options.body).value);
        puts.push({ url, method: options.method });
        if (reply instanceof Error) throw reply;
        return reply;
      },
      setTimeout: (fn) => { timers.push(fn); return timers.length; },
      clearTimeout: () => {},
    },
  );
  const flushTimers = () => { while (timers.length) timers.shift()(); };
  return { ctx, saved, puts, flushTimers };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

test('a popover change keeps the view mode and tile size the toolbar saved', async () => {
  const { ctx, saved, flushTimers } = loadPage();
  const popover = ctx.filePreferences();
  await popover.init();
  const toolbar = ctx.viewToggle();
  toolbar.init();
  toolbar.$ajax = () => {};

  await toolbar.setViewMode('mosaic');
  toolbar.setMosaicTileSize('5');
  await settle();
  popover.update('defaultSort', 'name');
  flushTimers();
  await settle();

  const last = saved.at(-1);
  assert.equal(last.defaultSort, 'name');
  assert.equal(last.defaultViewMode, 'mosaic');
  assert.equal(last.mosaicTileSize, 5);
  assert.equal(toolbar.viewMode, 'mosaic');
  assert.equal(toolbar.mosaicTileSize, 5);
});

test('saveFilePrefs puts the whole object and resolves to whether the server took it', async () => {
  const { ctx, saved, puts } = loadPage();
  ctx.updateFilePrefs({ defaultSort: 'size' });
  assert.equal(await ctx.saveFilePrefs(), true);
  assert.deepEqual(puts, [{ url: '/api/v1/settings/files/preferences', method: 'PUT' }]);
  assert.equal(saved.at(-1).defaultSort, 'size');

  assert.equal(await loadPage({ reply: { ok: false, status: 400 } }).ctx.saveFilePrefs(), false);
  assert.equal(await loadPage({ reply: new TypeError('offline') }).ctx.saveFilePrefs(), false);
});
