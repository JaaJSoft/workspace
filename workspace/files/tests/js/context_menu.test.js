const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/files/ui/static/files/ui/js/context_menu.js');

test('rotate and crop is left to the viewer, out of the file menu', () => {
  const menu = ctx.contextMenu();
  menu.nodeData = {
    actions: [
      { id: 'view', category: 'open' },
      { id: 'copy', category: 'edit' },
      { id: 'edit_image', category: 'edit' },
      { id: 'delete', category: 'danger' },
    ],
  };

  assert.deepStrictEqual(Array.from(menu.menuActions(), (a) => a.id), ['view', 'copy', 'delete']);
});

test('a menu with no node lists nothing', () => {
  assert.equal(ctx.contextMenu().menuActions().length, 0);
});
