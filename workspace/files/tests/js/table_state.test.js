'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function makeTable(embedded) {
  const fetched = [];
  const stored = {};
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/table.js', {
    _filePrefsCache: {},
    document: {
      createDocumentFragment: () => ({ appendChild() {} }),
      getElementById: (id) => (
        id === 'table-controls-data' && embedded !== undefined
          ? { textContent: embedded }
          : null
      ),
    },
    localStorage: {
      getItem: (key) => stored[key] ?? null,
      setItem: (key, value) => { stored[key] = value; },
    },
    fetch: (url) => {
      fetched.push(url);
      return Promise.resolve({ ok: false });
    },
  });
  const table = ctx.fileTableWithView();
  table.table = null;
  return { table, fetched, stored };
}

test('the saved controls embedded in the page are applied without a request', () => {
  const { table, fetched } = makeTable(JSON.stringify({ columnVisibility: { size: false } }));

  table.loadState();

  assert.equal(table.columnVisibility.size, false);
  assert.deepStrictEqual(fetched, []);
});

test('a user who never saved controls keeps the defaults without a request', () => {
  const { table, fetched } = makeTable('null');
  const defaults = [table.sortField, table.sortDir];

  table.loadState();

  assert.deepStrictEqual([table.sortField, table.sortDir], defaults);
  assert.deepStrictEqual(fetched, []);
});

test('the server copy wins over the one this browser kept', () => {
  const { table, stored } = makeTable(JSON.stringify({ columnVisibility: { size: false } }));
  stored[table.storageKey] = JSON.stringify({ columnVisibility: { size: true } });

  table.loadState();

  assert.equal(table.columnVisibility.size, false);
});
