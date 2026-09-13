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

test('only a file target offers user-to-user sharing', () => {
  const app = modal();
  app.nodeType = 'file';
  assert.equal(app.canShareWithPeople(), true);
  app.nodeType = 'folder';
  assert.equal(app.canShareWithPeople(), false);
});

test('user and group entries with the same id are distinct', () => {
  const app = modal();
  app.shares = [
    { type: 'user', id: 3, username: 'ann', permission: 'ro' },
    { type: 'group', id: 3, name: 'Team', permission: 'rw' },
  ];
  const keys = Array.from(app.displayList).map(e => e.key);
  assert.deepStrictEqual(keys, ['user:3', 'group:3']);

  app.stageRemove('group:3');
  const removed = Array.from(app.displayList).filter(e => e._removed).map(e => e.key);
  assert.deepStrictEqual(removed, ['group:3']);
});

test('a group already shared with is not offered again', () => {
  const app = modal();
  app.groups = [{ id: 1, name: 'Team' }, { id: 2, name: 'Ops' }];
  app.shares = [{ type: 'group', id: 1, name: 'Team', permission: 'ro' }];
  assert.deepStrictEqual(Array.from(app.selectableGroups()).map(g => g.id), [2]);

  app.stageAdd({ type: 'group', id: 2, name: 'Ops' });
  assert.deepStrictEqual(Array.from(app.selectableGroups()), []);
  assert.equal(app.pendingAdds.length, 1);
});

test('a share target maps to its request body', () => {
  const app = modal();
  assert.deepStrictEqual({ ...app.targetBody('group', 7) }, { group: 7 });
  assert.deepStrictEqual({ ...app.targetBody('user', 7) }, { shared_with: 7 });
  assert.deepStrictEqual({ ...app.splitKey('group:7') }, { type: 'group', id: '7' });
});
