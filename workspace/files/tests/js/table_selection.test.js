'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// Selection is painted onto the items by hand, and only onto the items whose
// state changed: a binding per item re-ran for the whole listing on each click.
function makeTable(uuids) {
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/table.js', {
    _filePrefsCache: {},
    document: { createDocumentFragment: () => ({ appendChild() {} }) },
  });
  const table = ctx.fileTableWithView();
  const items = uuids.map((uuid) => {
    const checkbox = { checked: false };
    const item = {
      uuid,
      checkbox,
      selected: false,
      paints: 0,
      dataset: { uuid },
      toggleAttribute(name, on) {
        assert.equal(name, 'data-selected');
        this.selected = on;
        this.paints += 1;
      },
      querySelector: (selector) => (selector === 'input[data-select-item]' ? checkbox : null),
    };
    return item;
  });
  table._itemsByUuid = new Map(items.map((item) => [item.uuid, item]));
  table._paintedSelection = new Set();
  return { table, items };
}

test('selecting marks the item and ticks its checkbox', () => {
  const { table, items } = makeTable(['a', 'b']);

  table.selectedUuids = new Set(['a']);
  table._paintSelection();

  assert.equal(items[0].selected, true);
  assert.equal(items[0].checkbox.checked, true);
  assert.equal(items[1].selected, false);
  assert.equal(items[1].checkbox.checked, false);
});

test('only the items whose selection changed are repainted', () => {
  const { table, items } = makeTable(['a', 'b', 'c']);
  table.selectedUuids = new Set(['a', 'b']);
  table._paintSelection();

  table.selectedUuids = new Set(['b', 'c']);
  table._paintSelection();

  assert.deepStrictEqual(items.map((item) => item.selected), [false, true, true]);
  assert.deepStrictEqual(items.map((item) => item.paints), [2, 1, 1]);
});

test('clearing the selection unmarks every selected item', () => {
  const { table, items } = makeTable(['a', 'b']);
  table.selectedUuids = new Set(['a', 'b']);
  table._paintSelection();

  table.selectedUuids = new Set();
  table._paintSelection();

  assert.deepStrictEqual(items.map((item) => item.checkbox.checked), [false, false]);
});

test('a selected uuid with no item on the page is ignored', () => {
  const { table } = makeTable(['a']);

  table.selectedUuids = new Set(['gone']);
  assert.doesNotThrow(() => table._paintSelection());
});
