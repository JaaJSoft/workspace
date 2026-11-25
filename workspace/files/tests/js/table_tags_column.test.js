'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function makeRow(dataset) {
  const cells = ['name', 'tags'].map((col) => ({ dataset: { col }, style: {} }));
  return { dataset, children: cells, tagsCell: cells[1] };
}

// A list view whose filter keeps `shown` in the tbody; the other rows are
// detached, the way applyRows() leaves them.
function makeListing(rows, shown) {
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/table.js', {
    _filePrefsCache: {},
  });
  const table = ctx.fileTableWithView();
  const header = makeRow({});
  table.columns = [{ id: 'name', required: true }, { id: 'tags' }];
  table.columnVisibility = {};
  table.originalRows = rows;
  table.tbody = {
    querySelector: () => null,
    querySelectorAll: () => shown.filter((row) => 'tags' in row.dataset),
  };
  table.table = { querySelectorAll: () => [header, ...shown] };
  return { table, header };
}

test('a tag on a filtered-out row keeps the Tags column', () => {
  const untagged = makeRow({ name: 'notes.txt', tags: ' ' });
  const tagged = makeRow({ name: 'report.txt', tags: 'tag-a ' });
  const { table, header } = makeListing([untagged, tagged], [untagged]);

  table.applyColumnVisibility();

  // Clearing the filter brings the tagged row back under a visible header.
  assert.equal(header.tagsCell.style.display, '');
  assert.equal(tagged.tagsCell.style.display, '');
});

test('hiding the Tags column reaches the filtered-out rows', () => {
  const shown = makeRow({ name: 'report.txt', tags: ' ' });
  const filteredOut = makeRow({ name: 'notes.txt', tags: ' ' });
  const { table, header } = makeListing([shown, filteredOut], [shown]);

  table.applyColumnVisibility();

  assert.equal(header.tagsCell.style.display, 'none');
  // Otherwise it comes back with one cell more than the header.
  assert.equal(filteredOut.tagsCell.style.display, 'none');
});
