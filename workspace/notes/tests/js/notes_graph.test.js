const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/notes/ui/static/notes/ui/js/notes_graph.js');
const nodeColorKey = ctx.notesGraph.nodeColorKey;
const fitZoom = ctx.notesGraph.fitZoom;
const linkActive = ctx.notesGraph.linkActive;

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

test('fitZoom caps zoom-in at MAX_ZOOM so a small graph is not blown up', () => {
  // tiny cloud in a large viewport would zoom in a lot, but is capped to MAX_ZOOM (2)
  assert.equal(fitZoom(60, 60, 800, 800, 40), 2);
});

test('fitZoom zooms out to fit a graph larger than the viewport', () => {
  // limited by the wider dimension: (800 - 80) / 2000 = 0.36
  assert.ok(Math.abs(fitZoom(2000, 1000, 800, 600, 40) - 0.36) < 1e-9);
});

test('fitZoom floors at 0.05 for an enormous graph', () => {
  assert.equal(fitZoom(1e9, 1e9, 800, 600, 40), 0.05);
});

test('linkActive: every link is active when nothing is hovered', () => {
  assert.equal(linkActive(null, new Set(), 'a', 'b'), true);
});

test('linkActive: active only when BOTH endpoints are in the neighbourhood', () => {
  const nb = new Set(['h', 'n1', 'n2']);
  assert.equal(linkActive('h', nb, 'h', 'n1'), true); // hovered <-> neighbour
  assert.equal(linkActive('h', nb, 'n1', 'n2'), true); // neighbour <-> neighbour
  assert.equal(linkActive('h', nb, 'n1', 'x'), false); // neighbour -> unrelated: dimmed
  assert.equal(linkActive('h', nb, 'x', 'y'), false); // unrelated link: dimmed
});
