const assert = require('node:assert');
const { test } = require('node:test');
const {
  loadScripts,
  CUSTOM_ELEMENT_STUBS,
} = require('../../../common/tests/js/loader');

// settings.js reads the shared <tag-chip> palette, which base.html loads
// before it.
function ctx() {
  return settingsWith({});
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

test('normalizeProjectKey trims and uppercases', () => {
  const { normalizeProjectKey } = ctx().projectSettingsHelpers;
  assert.equal(normalizeProjectKey('  core7 '), 'CORE7');
  assert.equal(normalizeProjectKey(null), '');
  assert.equal(normalizeProjectKey(undefined), '');
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

function settingsWith(extraGlobals) {
  return loadScripts(
    [
      'workspace/common/static/ui/js/tag_chip.js',
      'workspace/projects/ui/static/projects/ui/js/settings.js',
    ],
    { ...CUSTOM_ELEMENT_STUBS, getCSRFToken: () => 'test-token', ...extraGlobals }
  );
}

function generalWithFetch(fetchImpl) {
  return settingsWith({ fetch: fetchImpl }).projectSettingsGeneral({
    apiBase: '/api/v1/projects/p1',
  });
}

function generalWithData(data) {
  return settingsWith({
    document: {
      ...CUSTOM_ELEMENT_STUBS.document,
      getElementById: () => ({ textContent: JSON.stringify(data) }),
    },
  }).projectSettingsGeneral({ apiBase: '/api/v1/projects/p1' });
}

test('projectSettingsGeneral.init maps a null retention to the empty preset', () => {
  const c = generalWithData({
    name: 'P', description: '', key: 'P1', done_retention_days: null,
  });
  c.init();
  assert.equal(c.doneRetentionDays, '');
});

test('projectSettingsGeneral.init reads the stored retention as a string', () => {
  const c = generalWithData({
    name: 'P', description: '', key: 'P1', done_retention_days: 14,
  });
  c.init();
  assert.equal(c.doneRetentionDays, '14');
});

test('retentionSliderIndex maps always-visible to the last stop', () => {
  const { retentionSliderIndex } = ctx().projectSettingsHelpers;
  assert.equal(retentionSliderIndex(''), 5);
  assert.equal(retentionSliderIndex(null), 5);
});

test('retentionSliderIndex maps a preset to its stop', () => {
  const { retentionSliderIndex } = ctx().projectSettingsHelpers;
  assert.equal(retentionSliderIndex('1'), 0);
  assert.equal(retentionSliderIndex('7'), 1);
  assert.equal(retentionSliderIndex('90'), 4);
});

test('retentionSliderIndex snaps an API-set value to the nearest stop', () => {
  const { retentionSliderIndex } = ctx().projectSettingsHelpers;
  assert.equal(retentionSliderIndex('21'), 2);
  assert.equal(retentionSliderIndex('365'), 4);
});

test('retentionDaysFromIndex maps stops back to day strings', () => {
  const { retentionDaysFromIndex } = ctx().projectSettingsHelpers;
  assert.equal(retentionDaysFromIndex('0'), '1');
  assert.equal(retentionDaysFromIndex(3), '30');
  assert.equal(retentionDaysFromIndex(5), '');
});

test('projectSettingsGeneral.setRetentionIndex drives the canonical value', () => {
  const c = generalWithFetch(async () => ({ ok: true }));
  c.setRetentionIndex(1);
  assert.equal(c.doneRetentionDays, '7');
  c.setRetentionIndex('5');
  assert.equal(c.doneRetentionDays, '');
});

test('projectSettingsGeneral.retentionSliderLabel describes the current stop', () => {
  const c = generalWithFetch(async () => ({ ok: true }));
  c.doneRetentionDays = '1';
  assert.equal(c.retentionSliderLabel(), '1 day');
  c.doneRetentionDays = '30';
  assert.equal(c.retentionSliderLabel(), '30 days');
  c.doneRetentionDays = '';
  assert.equal(c.retentionSliderLabel(), 'Always');
});

test('projectSettingsGeneral.save sends the retention as a number', async () => {
  let captured = null;
  const c = generalWithFetch(async (url, options) => {
    captured = { url, options };
    return { ok: true };
  });
  c.name = 'P';
  c.key = 'P1';
  c.doneRetentionDays = '30';
  await c.save();
  assert.equal(JSON.parse(captured.options.body).done_retention_days, 30);
});

test('projectSettingsGeneral.save sends null for the always-visible preset', async () => {
  let captured = null;
  const c = generalWithFetch(async (url, options) => {
    captured = { url, options };
    return { ok: true };
  });
  c.name = 'P';
  c.key = 'P1';
  c.doneRetentionDays = '';
  await c.save();
  const body = JSON.parse(captured.options.body);
  assert.ok('done_retention_days' in body);
  assert.strictEqual(body.done_retention_days, null);
});

test('projectSettingsGeneral.init reads the estimate unit', () => {
  const c = generalWithData({
    name: 'P', description: '', key: 'P1', done_retention_days: null,
    estimate_unit: 'hours',
  });
  c.init();
  assert.equal(c.estimateUnit, 'hours');
});

test('projectSettingsGeneral.init maps a missing estimate unit to disabled', () => {
  const c = generalWithData({
    name: 'P', description: '', key: 'P1', done_retention_days: null,
  });
  c.init();
  assert.equal(c.estimateUnit, '');
});

test('projectSettingsGeneral.save sends the estimate unit', async () => {
  let captured = null;
  const c = generalWithFetch(async (url, options) => {
    captured = { url, options };
    return { ok: true };
  });
  c.name = 'P';
  c.key = 'P1';
  c.estimateUnit = 'points';
  await c.save();
  assert.equal(JSON.parse(captured.options.body).estimate_unit, 'points');
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
  return settingsWith({ fetch: fetchImpl }).projectGroupAccess({
    apiBase: '/api/v1/projects/p1',
  });
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

test('projectGroupAccess.addGroup is a no-op while a save is in flight', async () => {
  let called = 0;
  const c = groupAccess(async () => {
    called++;
    return { ok: true };
  });
  c.items = [{ id: 1, name: 'devs' }];
  c.busy = true;
  await c.addGroup({ id: 2, name: 'design' });
  assert.equal(called, 0);
  assert.deepEqual(c.items.map((g) => g.id), [1]);
});

test('projectGroupAccess.removeGroup is a no-op while a save is in flight', async () => {
  let called = 0;
  const c = groupAccess(async () => {
    called++;
    return { ok: true };
  });
  c.items = [{ id: 1, name: 'devs' }];
  c.busy = true;
  await c.removeGroup({ id: 1, name: 'devs' });
  assert.equal(called, 0);
  assert.deepEqual(c.items.map((g) => g.id), [1]);
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

test('projectEpics.saveEdit is a no-op when editing was cancelled', async () => {
  const c = ctx().projectEpics({ apiBase: '/x' });
  c.editing = null;
  c.editName = 'Nope';
  let called = false;
  c.request = async () => { called = true; };
  await c.saveEdit({ uuid: 'e1', name: 'Launch' });
  assert.equal(called, false);
});

test('projectEpics.toggleClosed flips the flag through the API', async () => {
  const c = ctx().projectEpics({ apiBase: '/x' });
  const calls = [];
  c.request = async (url, options) => {
    calls.push([url, JSON.parse(options.body)]);
    return { json: async () => ({}) };
  };
  c.epics = [];
  const epic = { uuid: 'e1', name: 'Launch', color: '', closed: false };
  c.items = [epic];
  await c.toggleClosed(epic);
  assert.deepStrictEqual(calls[0], ['/x/epics/e1', { closed: true }]);
  assert.equal(epic.closed, true);
  assert.deepStrictEqual({ ...c.epics[0] }, {
    uuid: 'e1',
    name: 'Launch',
    color: '',
    closed: true,
  });
});

test('projectEpics.progressPercent divides safely', () => {
  const c = ctx().projectEpics({ apiBase: '/x' });
  assert.equal(c.progressPercent({ task_count: 0, done_task_count: 0 }), 0);
  assert.equal(c.progressPercent({ task_count: 8, done_task_count: 2 }), 25);
  assert.equal(c.progressPercent({ task_count: 3, done_task_count: 3 }), 100);
});

test('projectEpics.saveDescEdit skips the API on an unchanged description', async () => {
  const c = ctx().projectEpics({ apiBase: '/x' });
  let called = false;
  c.request = async () => { called = true; };
  const epic = { uuid: 'e1', description: 'Ship v1' };
  c.startDescEdit(epic);
  c.descDraft = ' Ship v1 ';
  await c.saveDescEdit(epic);
  assert.equal(called, false);
  c.startDescEdit(epic);
  c.descDraft = 'Ship v2';
  await c.saveDescEdit(epic);
  assert.equal(called, true);
  assert.equal(epic.description, 'Ship v2');
});
