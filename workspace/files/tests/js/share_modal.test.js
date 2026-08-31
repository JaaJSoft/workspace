const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function modal() {
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/share_modal.js');
  return ctx.shareModal();
}

test('a file target offers no mode choice', () => {
  const app = modal();
  app.nodeType = 'file';
  assert.equal(app.canChooseMode(), false);
});

test('a folder target offers the three modes', () => {
  const app = modal();
  app.nodeType = 'folder';
  assert.equal(app.canChooseMode(), true);
  assert.deepStrictEqual(
    Array.from(app.availableModes()).map(m => m.value),
    ['read', 'drop', 'both'],
  );
});

test('the caps only apply to a mode that accepts uploads', () => {
  const app = modal();
  app.nodeType = 'folder';
  app.newLinkMode = 'read';
  assert.equal(app.showsCaps(), false);
  app.newLinkMode = 'drop';
  assert.equal(app.showsCaps(), true);
  app.newLinkMode = 'both';
  assert.equal(app.showsCaps(), true);
});

test('a link mode renders a human label', () => {
  const app = modal();
  assert.equal(app.modeLabel('drop'), 'Upload only');
});
