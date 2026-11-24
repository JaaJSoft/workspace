'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// Every folder swap builds the listing component again: what one instance
// registered on window must leave with it, or the listeners pile up.
function makeContext() {
  const listeners = new Map();
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/table.js', {
    _filePrefsCache: {},
    _tableControlsCache: null,
    fileClipboard: { hasItems: () => false },
    getFilePrefs: () => ({}),
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(handler);
    },
    removeEventListener(type, handler) {
      listeners.get(type)?.delete(handler);
    },
  });
  const count = () => Array.from(listeners.values()).reduce((sum, set) => sum + set.size, 0);
  return { ctx, count, listeners };
}

function mount(ctx) {
  const table = ctx.fileTableWithView();
  table.$el = { querySelector: () => null };
  table.$watch = () => {};
  table.cards = () => [];
  table.fetchActions = () => {};
  table.applyAll = () => {};
  table._keepState = () => {};
  table._takeKeptState = () => null;
  table.init();
  return table;
}

test('destroying the listing removes every window listener it added', () => {
  const { ctx, count } = makeContext();
  const before = count();

  const table = mount(ctx);
  assert.ok(count() > before);

  table.destroy();
  assert.equal(count(), before);
  assert.equal(ctx._fileShortcutHandler, null);
});

test('a listing destroyed after the next one mounted leaves its shortcuts alone', () => {
  const { ctx, listeners } = makeContext();
  const old = mount(ctx);
  const current = mount(ctx);
  const handler = ctx._fileShortcutHandler;

  old.destroy();

  assert.equal(ctx._fileShortcutHandler, handler);
  assert.ok(listeners.get('keydown').has(handler));
  current.destroy();
  assert.equal(ctx._fileShortcutHandler, null);
});

test('remounting the listing does not add listeners on top of the previous one', () => {
  const { ctx, count } = makeContext();

  mount(ctx).destroy();
  const afterOne = count();
  for (let i = 0; i < 5; i++) mount(ctx).destroy();

  assert.equal(count(), afterOne);
});
