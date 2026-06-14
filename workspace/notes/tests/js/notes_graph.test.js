const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/notes/ui/static/notes/ui/js/notes_graph.js');
const nodeColorKey = ctx.notesGraph.nodeColorKey;

test('favorite takes precedence over journal and regular', () => {
  assert.equal(nodeColorKey({ is_favorite: true, parent: 'J' }, 'J'), 'favorite');
});

test('journal when parent is the journal folder and not favorite', () => {
  assert.equal(nodeColorKey({ is_favorite: false, parent: 'J' }, 'J'), 'journal');
});

test('regular otherwise', () => {
  assert.equal(nodeColorKey({ is_favorite: false, parent: 'X' }, 'J'), 'regular');
  assert.equal(nodeColorKey({ is_favorite: false, parent: null }, 'J'), 'regular');
});

test('no journal folder configured -> never journal', () => {
  assert.equal(nodeColorKey({ is_favorite: false, parent: 'J' }, null), 'regular');
});
