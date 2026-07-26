const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function ctx() {
  return loadScript('workspace/projects/ui/static/projects/ui/js/settings.js', {
    getCSRFToken: () => 'test-token',
  });
}

const COLUMNS = [
  { uuid: 'a', name: 'Backlog', category: 'backlog' },
  { uuid: 'b', name: 'To do', category: 'active' },
  { uuid: 'c', name: 'In progress', category: 'active' },
  { uuid: 'd', name: 'Done', category: 'done' },
];

test('defaultMoveTarget prefers previous sibling of same category', () => {
  const { defaultMoveTarget } = ctx().projectSettingsHelpers;
  assert.equal(defaultMoveTarget(COLUMNS, 'c'), 'b');
});

test('defaultMoveTarget falls back to next sibling of same category', () => {
  const { defaultMoveTarget } = ctx().projectSettingsHelpers;
  assert.equal(defaultMoveTarget(COLUMNS, 'b'), 'c');
});

test('defaultMoveTarget returns null when category has no sibling', () => {
  const { defaultMoveTarget } = ctx().projectSettingsHelpers;
  assert.equal(defaultMoveTarget(COLUMNS, 'a'), null);
  assert.equal(defaultMoveTarget(COLUMNS, 'unknown'), null);
});

test('saveEdit is a no-op when editing was cancelled', async () => {
  const c = ctx().projectColumns({ apiBase: '/x' });
  c.editing = null;
  c.editName = 'Nope';
  let called = false;
  c.request = async () => { called = true; };
  await c.saveEdit({ uuid: 'a', name: 'To do' });
  assert.equal(called, false);
});

test('projectMembers.changeRole refetches members when the server refuses', async () => {
  const c = ctx().projectMembers({ apiBase: '/x' });
  let refetched = false;
  c.init = async () => { refetched = true; };
  c.request = async () => { throw new Error('Cannot demote the last admin of a project.'); };
  await c.changeRole({ uuid: 'm1', role: 'admin' }, 'member');
  assert.equal(refetched, true);
  assert.equal(c.error, 'Cannot demote the last admin of a project.');
});

test('projectLabels.saveEdit is a no-op when editing was cancelled', async () => {
  const c = ctx().projectLabels({ apiBase: '/x' });
  c.editing = null;
  c.editName = 'Nope';
  let called = false;
  c.request = async () => { called = true; };
  await c.saveEdit({ uuid: 'l1', name: 'bug' });
  assert.equal(called, false);
});
