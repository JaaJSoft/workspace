// Where a context menu lands. The measuring half needs a layout and is left
// to the browser; the arithmetic that decides the corner is here.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const menu = loadScript('workspace/vault/ui/static/vault/ui/js/vault_menu.js').vaultMenu;
const VIEWPORT = { width: 1000, height: 800 };

test('a menu that fits opens exactly where the click was', () => {
  assert.deepEqual({ ...menu.clamp(300, 200, 224, 180, VIEWPORT) }, { x: 300, y: 200 });
});

test('a click near the right edge pulls the menu back inside the page', () => {
  const { x } = menu.clamp(950, 200, 224, 180, VIEWPORT);
  assert.ok(x + 224 <= VIEWPORT.width, 'the whole panel must be on screen');
  assert.equal(x, 1000 - 224 - 8);
});

test('a click near the bottom edge lifts the menu instead of cutting it off', () => {
  const { y } = menu.clamp(300, 780, 224, 180, VIEWPORT);
  assert.equal(y, 800 - 180 - 8);
});

test('a menu taller than the viewport starts at the margin rather than off-screen', () => {
  // Clamping to the far edge alone would give a negative coordinate, which
  // hides the rows the user is most likely to want.
  assert.deepEqual({ ...menu.clamp(300, 400, 224, 900, VIEWPORT) }, { x: 300, y: 8 });
});
