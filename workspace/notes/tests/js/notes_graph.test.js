const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/notes/ui/static/notes/ui/js/notes_graph.js');
const nodeColorKey = ctx.notesGraph.nodeColorKey;
const escapeHtml = ctx.notesGraph.escapeHtml;
const withAlpha = ctx._withAlpha;

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

test('withAlpha applies alpha to daisyUI oklch colors and to rgb', () => {
  // daisyUI themes resolve to oklch(); search dimming was a no-op because the
  // old rgb-only helper returned oklch unchanged.
  assert.equal(withAlpha('oklch(0.648 0.15 160)', 0.15), 'oklch(0.648 0.15 160 / 0.15)');
  assert.equal(withAlpha('rgb(10, 20, 30)', 0.2), 'rgba(10, 20, 30, 0.2)');
});

test('escapeHtml neutralizes HTML in node names (XSS guard)', () => {
  assert.equal(
    escapeHtml('<img src=x onerror=alert(1)>'),
    '&lt;img src=x onerror=alert(1)&gt;'
  );
  assert.equal(escapeHtml('a & "b"'), 'a &amp; &quot;b&quot;');
  assert.equal(escapeHtml(null), '');
});
