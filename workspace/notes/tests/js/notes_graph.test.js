const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/notes/ui/static/notes/ui/js/notes_graph.js');
const nodeColorKey = ctx.notesGraph.nodeColorKey;
const escapeHtml = ctx.notesGraph.escapeHtml;

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

test('escapeHtml neutralizes HTML in node names (XSS guard)', () => {
  assert.equal(
    escapeHtml('<img src=x onerror=alert(1)>'),
    '&lt;img src=x onerror=alert(1)&gt;'
  );
  assert.equal(escapeHtml('a & "b"'), 'a &amp; &quot;b&quot;');
  assert.equal(escapeHtml(null), '');
});
