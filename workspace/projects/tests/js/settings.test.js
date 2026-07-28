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

// Alpine treats destroy() as a lifecycle hook and auto-invokes it when the
// element leaves the DOM (e.g. an alpine-ajax view swap). An action named
// destroy() therefore fires on navigation - the delete-project dialog used
// to pop up when leaving the settings page.
test('projectSettingsDanger does not expose a destroy() lifecycle collision', () => {
  const c = ctx().projectSettingsDanger({ apiBase: '/x', projectName: 'P' });
  assert.equal(typeof c.destroy, 'undefined');
  assert.equal(typeof c.deleteProject, 'function');
});

function groupAccess(fetchImpl) {
  return loadScript('workspace/projects/ui/static/projects/ui/js/settings.js', {
    getCSRFToken: () => 'test-token',
    fetch: fetchImpl,
  }).projectGroupAccess({ apiBase: '/api/v1/projects/p1' });
}

test('projectGroupAccess.addGroup PATCHes the extended id list and appends', async () => {
  let captured = null;
  const c = groupAccess(async (url, options) => {
    captured = { url, options };
    return { ok: true };
  });
  c.items = [{ id: 1, name: 'devs' }];
  await c.addGroup({ id: 2, name: 'design' });
  assert.equal(captured.url, '/api/v1/projects/p1');
  assert.equal(captured.options.method, 'PATCH');
  assert.deepEqual(JSON.parse(captured.options.body).groups, [1, 2]);
  assert.deepEqual(c.items.map((g) => g.name), ['devs', 'design']);
  assert.equal(c.saved, true);
});

test('projectGroupAccess.addGroup skips groups that are already attached', async () => {
  let called = false;
  const c = groupAccess(async () => {
    called = true;
    return { ok: true };
  });
  c.items = [{ id: 1, name: 'devs' }];
  await c.addGroup({ id: '1', name: 'devs' });
  assert.equal(called, false);
  assert.equal(c.items.length, 1);
});

test('projectGroupAccess.removeGroup PATCHes the reduced id list', async () => {
  let captured = null;
  const c = groupAccess(async (url, options) => {
    captured = { url, options };
    return { ok: true };
  });
  c.items = [
    { id: 1, name: 'devs' },
    { id: 2, name: 'design' },
  ];
  await c.removeGroup({ id: 1, name: 'devs' });
  assert.deepEqual(JSON.parse(captured.options.body).groups, [2]);
  assert.deepEqual(c.items.map((g) => g.name), ['design']);
});

test('projectGroupAccess.save surfaces the server field error and keeps items', async () => {
  const c = groupAccess(async () => ({
    ok: false,
    json: async () => ({ groups: ['You can only attach groups you belong to.'] }),
  }));
  c.items = [{ id: 1, name: 'devs' }];
  await c.addGroup({ id: 2, name: 'design' });
  assert.equal(c.error, 'You can only attach groups you belong to.');
  assert.equal(c.saved, false);
  assert.equal(c.busy, false);
  assert.deepEqual(c.items.map((g) => g.name), ['devs']);
});

test('projectGroupAccess.selectableGroups excludes attached groups', () => {
  const c = groupAccess(async () => ({ ok: true }));
  c.items = [{ id: 1, name: 'devs' }];
  c.available = [
    { id: 1, name: 'devs' },
    { id: 2, name: 'design' },
  ];
  assert.deepEqual(
    Array.from(c.selectableGroups()).map((g) => g.name),
    ['design']
  );
});

test('projectMembers.addMember skips users that are already members', async () => {
  const c = ctx().projectMembers({ apiBase: '/x' });
  c.items = [{ uuid: 'm1', user: 7, username: 'alice', role: 'member' }];
  let called = false;
  c.request = async () => { called = true; };
  await c.addMember({ id: '7', username: 'alice' });
  assert.equal(called, false);
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

test('projectColumns.saveEdit trims the name before saving', async () => {
  const c = ctx().projectColumns({ apiBase: '/x' });
  const column = { uuid: 'a', name: 'To do' };
  c.editing = 'a';
  c.editName = '  Doing  ';
  let body = null;
  c.request = async (url, options) => { body = JSON.parse(options.body); };
  await c.saveEdit(column);
  assert.equal(body.name, 'Doing');
  assert.equal(column.name, 'Doing');
});

test('projectColumns.saveEdit treats a whitespace-padded same name as unchanged', async () => {
  const c = ctx().projectColumns({ apiBase: '/x' });
  const column = { uuid: 'a', name: 'To do' };
  c.editing = 'a';
  c.editName = ' To do ';
  let called = false;
  c.request = async () => { called = true; };
  await c.saveEdit(column);
  assert.equal(called, false);
  assert.equal(c.editing, null);
});
