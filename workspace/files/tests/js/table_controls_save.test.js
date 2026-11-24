'use strict';

// The table's column order and visibility are put under files/table_controls,
// debounced, and a state the server already took is not sent again.
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

function makeTable(reply = { ok: true, status: 200 }) {
  const puts = [];
  const timers = [];
  const ctx = loadScripts([
    'workspace/core/static/core/js/user_settings.js',
    'workspace/files/ui/static/files/ui/js/table.js',
  ], {
    _filePrefsCache: {},
    getCSRFToken: () => 'token',
    document: { createDocumentFragment: () => ({ appendChild() {} }), getElementById: () => null },
    localStorage: { getItem: () => null, setItem: () => {} },
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    clearTimeout: () => {},
    fetch: (url, options) => {
      puts.push({ url, method: options.method, body: JSON.parse(options.body) });
      return reply instanceof Error ? Promise.reject(reply) : Promise.resolve(reply);
    },
  });
  const table = ctx.fileTableWithView();
  table.table = null;
  table._initializing = false;
  table.columnOrder = ['name', 'size'];
  table.columnVisibility = { size: false };
  const save = async () => {
    table.saveState();
    while (timers.length) timers.shift()();
    await new Promise((resolve) => setImmediate(resolve));
  };
  return { ctx, table, puts, save };
}

test('the column state is put under files/table_controls', async () => {
  const { ctx, puts, save } = makeTable();
  await save();

  assert.equal(puts.length, 1);
  assert.equal(puts[0].url, '/api/v1/settings/files/table_controls');
  assert.equal(puts[0].method, 'PUT');
  assert.deepEqual(puts[0].body, { value: { columnOrder: ['name', 'size'], columnVisibility: { size: false } } });
  assert.deepEqual({ ...ctx._tableControlsCache }, { columnOrder: ['name', 'size'], columnVisibility: { size: false } });
});

test('a state the server took is not sent again', async () => {
  const { puts, save } = makeTable();
  await save();
  await save();
  assert.equal(puts.length, 1);
});

test('a state lost to the network is sent again on the next save', async () => {
  const { puts, save } = makeTable(new TypeError('offline'));
  await save();
  await save();
  assert.equal(puts.length, 2);
});

test('a state the server refused is sent again on the next save', async () => {
  const { puts, save } = makeTable({ ok: false, status: 400 });
  await save();
  await save();
  assert.equal(puts.length, 2);
});
