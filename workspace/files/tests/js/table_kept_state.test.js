'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// A re-render of the listing builds the component again; what the user was
// doing in it is handed from one instance to the next by view (#1821).
function makeContext() {
  return loadScript('workspace/files/ui/static/files/ui/js/table.js', {
    _filePrefsCache: {},
    document: { createDocumentFragment: () => ({ appendChild() {} }) },
  });
}

function makeTable(ctx, view, uuids) {
  const table = ctx.fileTableWithView();
  table._root = {
    closest: (selector) => (selector === '#folder-browser' && view !== null
      ? { dataset: { viewUrl: view } }
      : null),
  };
  table._itemsByUuid = new Map(uuids.map((uuid) => [uuid, { dataset: { uuid } }]));
  return table;
}

function select(table, uuids, last) {
  table.selectedUuids = new Set(uuids);
  table.lastSelectedUuid = last;
  table._keepState();
}

test('the next instance of the same view takes the selection and the filters back', () => {
  const ctx = makeContext();
  const before = makeTable(ctx, '/files', ['a', 'b', 'c']);
  before.searchQuery = 'rep';
  before.typeFilter = 'files';
  before.tagFilter = ['t1'];
  select(before, ['a', 'c'], 'c');

  const after = makeTable(ctx, '/files', ['a', 'b', 'c']);
  const kept = after._takeKeptState();
  after._applyKeptFilters(kept);
  after._applyKeptSelection(kept);

  assert.deepStrictEqual(Array.from(after.selectedUuids), ['a', 'c']);
  assert.equal(after.lastSelectedUuid, 'c');
  assert.equal(after.searchQuery, 'rep');
  assert.equal(after.typeFilter, 'files');
  assert.deepStrictEqual(Array.from(after.tagFilter), ['t1']);
});

test('an item the new listing no longer holds drops out of the selection', () => {
  const ctx = makeContext();
  select(makeTable(ctx, '/files', ['a', 'b']), ['a', 'b'], 'b');

  const after = makeTable(ctx, '/files', ['a']);
  after._applyKeptSelection(after._takeKeptState());

  assert.deepStrictEqual(Array.from(after.selectedUuids), ['a']);
  assert.equal(after.lastSelectedUuid, null);
});

test('another view takes nothing back', () => {
  const ctx = makeContext();
  select(makeTable(ctx, '/files', ['a']), ['a'], 'a');

  assert.equal(makeTable(ctx, '/files?favorites=1', ['a'])._takeKeptState(), null);
});

test('rendering another view in between starts the first one afresh', () => {
  const ctx = makeContext();
  select(makeTable(ctx, '/files', ['a']), ['a'], 'a');
  makeTable(ctx, '/files/folder', ['x'])._keepState();

  assert.equal(makeTable(ctx, '/files', ['a'])._takeKeptState(), null);
});

test('a listing outside the file browser keeps nothing', () => {
  const ctx = makeContext();
  const table = makeTable(ctx, null, ['a']);
  select(table, ['a'], 'a');

  assert.equal(ctx._fileListingKept, undefined);
  assert.equal(table._takeKeptState(), null);
});
