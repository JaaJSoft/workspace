const assert = require('node:assert');
const { test } = require('node:test');

const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/projects/ui/static/projects/ui/js/board.js', {
  URL,
});

function fakeList(uuids) {
  return {
    querySelectorAll: (selector) => {
      assert.equal(selector, '[data-task-uuid]');
      return uuids.map((uuid) => ({ dataset: { taskUuid: uuid } }));
    },
  };
}

test('listOrder reads task uuids in DOM order', () => {
  const result = Array.from(
    ctx.projectBoardHelpers.listOrder(fakeList(['a', 'b', 'c']))
  );
  assert.deepStrictEqual(result, ['a', 'b', 'c']);
});

test('listOrder returns an empty order for an empty column', () => {
  const result = Array.from(ctx.projectBoardHelpers.listOrder(fakeList([])));
  assert.deepStrictEqual(result, []);
});

function panelDeleteBoard(calls) {
  ctx.getCSRFToken = () => 'token';
  ctx.localStorage = { getItem: () => null, setItem: () => {} };
  ctx.location = { href: 'http://x.test/projects/p/board?task=u1' };
  ctx.history = {
    pushState: () => {},
    replaceState: () => {},
  };
  const board = ctx.projectBoard({
    apiBase: '/api',
    projectBase: '/projects/p',
    writable: true,
  });
  board.panelTaskUuid = 'u1';
  board.refresh = () => calls.push('refresh');
  return board;
}

test('deletePanelTask aborts without a request when declined', async () => {
  const calls = [];
  ctx.AppDialog = {
    confirm: async () => {
      calls.push('confirm');
      return false;
    },
  };
  ctx.fetch = async (url, opts) => {
    calls.push(opts.method + ' ' + url);
    return { ok: true };
  };
  const board = panelDeleteBoard(calls);
  await board.deletePanelTask('u1', 'Task one');
  assert.deepStrictEqual(Array.from(calls), ['confirm']);
});

test('deletePanelTask deletes, closes the panel, and refreshes', async () => {
  const calls = [];
  ctx.AppDialog = {
    confirm: async () => {
      calls.push('confirm');
      return true;
    },
  };
  ctx.fetch = async (url, opts) => {
    calls.push(opts.method + ' ' + url);
    return { ok: true };
  };
  const board = panelDeleteBoard(calls);
  await board.deletePanelTask('u1', 'Task one');
  assert.equal(board.panelTaskUuid, null);
  assert.deepStrictEqual(Array.from(calls), [
    'confirm',
    'DELETE /api/tasks/u1',
    'refresh',
  ]);
});

test('_closeDrawerOnMobile unchecks drawer when on mobile', () => {
  ctx.getCSRFToken = () => 'token';
  ctx.localStorage = { getItem: () => null, setItem: () => {} };
  const checkboxState = { checked: true };
  ctx.document = {
    getElementById: (id) => {
      if (id === 'projects-drawer') return checkboxState;
      return null;
    },
  };
  const board = ctx.projectBoard({ apiBase: '/api', writable: true });
  board.isMobile = () => true;
  board._closeDrawerOnMobile();
  assert.equal(checkboxState.checked, false);
});

test('_closeDrawerOnMobile does nothing when not on mobile', () => {
  ctx.getCSRFToken = () => 'token';
  ctx.localStorage = { getItem: () => null, setItem: () => {} };
  const checkboxState = { checked: true };
  ctx.document = {
    getElementById: (id) => {
      if (id === 'projects-drawer') return checkboxState;
      return null;
    },
  };
  const board = ctx.projectBoard({ apiBase: '/api', writable: true });
  board.isMobile = () => false;
  board._closeDrawerOnMobile();
  assert.equal(checkboxState.checked, true);
});

test('taskParamUrl adds the task param', () => {
  assert.equal(
    ctx.projectBoardHelpers.taskParamUrl('http://x.test/projects/p/board', 'u1'),
    '/projects/p/board?task=u1'
  );
});

test('taskParamUrl returns null when the param is already correct', () => {
  assert.equal(
    ctx.projectBoardHelpers.taskParamUrl(
      'http://x.test/projects/p/board?task=u1',
      'u1'
    ),
    null
  );
});

test('taskParamUrl removes the task param when uuid is null', () => {
  assert.equal(
    ctx.projectBoardHelpers.taskParamUrl(
      'http://x.test/projects/p/board?task=u1',
      null
    ),
    '/projects/p/board'
  );
  assert.equal(
    ctx.projectBoardHelpers.taskParamUrl('http://x.test/projects/p/board', null),
    null
  );
});

test('taskMatchesFilters matches everything with empty filters', () => {
  const match = ctx.projectBoardHelpers.taskMatchesFilters;
  const empty = { q: '', assignee: '', label: '', priority: '' };
  assert.equal(match({}, empty), true);
  assert.equal(match({ search: 'fix login', priority: 'high' }, empty), true);
});

test('taskMatchesFilters searches case-insensitively', () => {
  const match = ctx.projectBoardHelpers.taskMatchesFilters;
  const dataset = { search: 'fix the login flow bug' };
  assert.equal(match(dataset, { q: 'LOGIN' }), true);
  assert.equal(match(dataset, { q: '  bug ' }), true);
  assert.equal(match(dataset, { q: 'payment' }), false);
});

test('taskMatchesFilters filters by priority, label and assignee', () => {
  const match = ctx.projectBoardHelpers.taskMatchesFilters;
  const dataset = { priority: 'high', labels: 'l1 l2', assignees: '7 9' };
  assert.equal(match(dataset, { priority: 'high' }), true);
  assert.equal(match(dataset, { priority: 'low' }), false);
  assert.equal(match(dataset, { label: 'l2' }), true);
  assert.equal(match(dataset, { label: 'l3' }), false);
  assert.equal(match(dataset, { assignee: '9' }), true);
  assert.equal(match(dataset, { assignee: '8' }), false);
});

test('taskMatchesFilters assignee "none" matches only unassigned tasks', () => {
  const match = ctx.projectBoardHelpers.taskMatchesFilters;
  assert.equal(match({ assignees: '' }, { assignee: 'none' }), true);
  assert.equal(match({}, { assignee: 'none' }), true);
  assert.equal(match({ assignees: '7' }, { assignee: 'none' }), false);
});

test('taskMatchesFilters requires every active filter to match', () => {
  const match = ctx.projectBoardHelpers.taskMatchesFilters;
  const dataset = { search: 'fix login', priority: 'high', labels: 'l1' };
  assert.equal(match(dataset, { q: 'login', priority: 'high' }), true);
  assert.equal(match(dataset, { q: 'login', priority: 'low' }), false);
});

test('filtersActive and clearFilters track filter state', () => {
  const board = panelBoard();
  assert.equal(board.filtersActive(), false);
  board.filters.q = '   ';
  assert.equal(board.filtersActive(), false);
  board.filters.q = 'bug';
  assert.equal(board.filtersActive(), true);
  board.clearFilters();
  assert.equal(board.filtersActive(), false);
  board.filters.assignee = 'none';
  assert.equal(board.filtersActive(), true);
});

test('toggleSelect adds then removes a uuid', () => {
  const board = panelBoard();
  board.toggleSelect('u1');
  board.toggleSelect('u2');
  assert.deepStrictEqual(Array.from(board.selected), ['u1', 'u2']);
  assert.equal(board.isSelected('u1'), true);
  board.toggleSelect('u1');
  assert.deepStrictEqual(Array.from(board.selected), ['u2']);
  board.clearSelection();
  assert.deepStrictEqual(Array.from(board.selected), []);
});

function backlogDom(rows) {
  return {
    querySelectorAll: (selector) => {
      assert.equal(selector, '#backlog [data-task-uuid]');
      return rows.map(([uuid, dataset]) => ({
        dataset: { taskUuid: uuid, ...dataset },
      }));
    },
  };
}

test('toggleSelectAll selects only the rows matching the filters', () => {
  ctx.document = backlogDom([
    ['u1', { priority: 'high' }],
    ['u2', { priority: 'low' }],
    ['u3', { priority: 'high' }],
  ]);
  const board = panelBoard();
  board.filters.priority = 'high';
  board.toggleSelectAll();
  assert.deepStrictEqual(Array.from(board.selected), ['u1', 'u3']);
  assert.equal(board.allVisibleSelected(), true);
  board.toggleSelectAll();
  assert.deepStrictEqual(Array.from(board.selected), []);
});

test('toggleSelectAll keeps selections outside the current filter', () => {
  ctx.document = backlogDom([
    ['u1', { priority: 'high' }],
    ['u2', { priority: 'low' }],
    ['u3', { priority: 'high' }],
  ]);
  const board = panelBoard();
  board.filters.priority = 'high';
  board.selected = ['u2'];
  board.toggleSelectAll();
  assert.deepStrictEqual(Array.from(board.selected), ['u2', 'u1', 'u3']);
  board.toggleSelectAll();
  assert.deepStrictEqual(Array.from(board.selected), ['u2']);
});

test('toggleSelectAll leaves the selection alone when nothing is visible', () => {
  ctx.document = backlogDom([['u1', { priority: 'high' }]]);
  const board = panelBoard();
  board.filters.priority = 'low';
  board.selected = ['u1'];
  board.toggleSelectAll();
  assert.deepStrictEqual(Array.from(board.selected), ['u1']);
});

test('columnCount returns the server total when no filter is active', () => {
  const board = panelBoard();
  assert.equal(board.columnCount('s1', 5), 5);
});

test('columnCount counts only matching cards when filtering', () => {
  ctx.document = {
    querySelectorAll: (selector) => {
      assert.equal(selector, '[data-status-uuid="s1"] [data-task-uuid]');
      return [
        { dataset: { taskUuid: 'u1', priority: 'high' } },
        { dataset: { taskUuid: 'u2', priority: 'low' } },
      ];
    },
  };
  const board = panelBoard();
  board.filters.priority = 'high';
  assert.equal(board.columnCount('s1', 2), 1);
});

test('moveTasks posts to the bulk endpoint and prunes the selection', async () => {
  const calls = [];
  ctx.fetch = async (url, opts) => {
    calls.push(opts.method + ' ' + url + ' ' + opts.body);
    return { ok: true };
  };
  const board = panelBoard();
  board.refresh = () => calls.push('refresh');
  board.selected = ['u1', 'u2', 'u3'];
  await board.moveTasks(['u1', 'u3'], 's-active');
  assert.deepStrictEqual(Array.from(calls), [
    'POST /api/tasks/move {"status":"s-active","tasks":["u1","u3"]}',
    'refresh',
  ]);
  assert.deepStrictEqual(Array.from(board.selected), ['u2']);
});

test('moveTasks keeps the selection and alerts on failure', async () => {
  const alerts = [];
  ctx.AppAlert = { error: (message) => alerts.push(message) };
  ctx.fetch = async () => ({ ok: false });
  const board = panelBoard();
  const refreshes = [];
  board.refresh = () => refreshes.push(1);
  board.selected = ['u1'];
  await board.moveTasks(['u1'], 's-active');
  assert.deepStrictEqual(Array.from(board.selected), ['u1']);
  assert.deepStrictEqual(Array.from(alerts), ['Could not move the tasks.']);
  assert.equal(refreshes.length, 1);
});

test('sendToBoard moves the single task to the first active status', async () => {
  const calls = [];
  ctx.fetch = async (url, opts) => {
    calls.push(opts.method + ' ' + url + ' ' + opts.body);
    return { ok: true };
  };
  const board = panelBoard();
  board.refresh = () => {};
  board.statuses = [
    { uuid: 's-backlog', category: 'backlog' },
    { uuid: 's-todo', category: 'active' },
    { uuid: 's-done', category: 'done' },
  ];
  await board.sendToBoard('u1');
  assert.deepStrictEqual(Array.from(calls), [
    'POST /api/tasks/move {"status":"s-todo","tasks":["u1"]}',
  ]);
});

test('sendSelected defaults to the first active status', async () => {
  const calls = [];
  ctx.fetch = async (url, opts) => {
    calls.push(opts.method + ' ' + url + ' ' + opts.body);
    return { ok: true };
  };
  const board = panelBoard();
  board.refresh = () => {};
  board.statuses = [
    { uuid: 's-backlog', category: 'backlog' },
    { uuid: 's-todo', category: 'active' },
  ];
  board.selected = ['u1', 'u2'];
  await board.sendSelected();
  assert.deepStrictEqual(Array.from(calls), [
    'POST /api/tasks/move {"status":"s-todo","tasks":["u1","u2"]}',
  ]);
  assert.deepStrictEqual(Array.from(board.selected), []);
});

test('sendSelected honours an explicit target status', async () => {
  const calls = [];
  ctx.fetch = async (url, opts) => {
    calls.push(opts.method + ' ' + url + ' ' + opts.body);
    return { ok: true };
  };
  const board = panelBoard();
  board.refresh = () => {};
  board.selected = ['u1'];
  await board.sendSelected('s-done');
  assert.deepStrictEqual(Array.from(calls), [
    'POST /api/tasks/move {"status":"s-done","tasks":["u1"]}',
  ]);
});

test('boardStatuses excludes backlog columns', () => {
  const board = panelBoard();
  board.statuses = [
    { uuid: 's-backlog', category: 'backlog' },
    { uuid: 's-todo', category: 'active' },
    { uuid: 's-done', category: 'done' },
  ];
  assert.deepStrictEqual(
    Array.from(board.boardStatuses()).map((s) => s.uuid),
    ['s-todo', 's-done']
  );
});

test('fieldAction maps each editable field to its action id', () => {
  const cases = {
    title: 'edit',
    description: 'edit',
    priority: 'edit',
    status: 'move',
    due_date: 'set_due',
    assignees: 'assign',
    labels: 'set_labels',
  };
  for (const [field, action] of Object.entries(cases)) {
    assert.equal(ctx.projectBoardHelpers.fieldAction(field), action);
  }
});

function panelWithActions(actions, calls) {
  const panel = ctx.taskPanel();
  panel.data = {
    uuid: 'u1',
    title: 'Task one',
    description: 'desc',
    status: 's1',
    priority: 'medium',
    due_date: '',
    assignees: ['7'],
    labels: [],
  };
  panel.actions = actions;
  panel.patchTask = (uuid, patch) => {
    calls.push([uuid, patch]);
  };
  panel.deletePanelTask = (uuid, title) => {
    calls.push(['delete', uuid, title]);
  };
  return panel;
}

test('startEdit is blocked without the matching action', () => {
  const panel = panelWithActions([], []);
  panel.startEdit('title', 'Task one');
  assert.equal(panel.editing, null);
});

test('commitDraft patches only when the value changed', () => {
  const calls = [];
  const panel = panelWithActions(['edit'], calls);
  panel.startEdit('title', panel.data.title);
  panel.draft = 'Renamed';
  panel.commitDraft('title');
  // The patch object is built inside the vm realm; spread it so
  // deepStrictEqual compares structure, not prototypes.
  assert.deepStrictEqual(
    [calls[0][0], { ...calls[0][1] }],
    ['u1', { title: 'Renamed' }]
  );

  panel.startEdit('title', panel.data.title);
  panel.commitDraft('title');
  assert.equal(calls.length, 1);
});

test('commitDraft flattens newlines pasted into the title', () => {
  const calls = [];
  const panel = panelWithActions(['edit'], calls);
  panel.startEdit('title', panel.data.title);
  panel.draft = 'Line one\nLine two\r\n  Line three';
  panel.commitDraft('title');
  assert.deepStrictEqual(
    [calls[0][0], { ...calls[0][1] }],
    ['u1', { title: 'Line one Line two Line three' }]
  );
});

test('commitDraft refuses an empty title', () => {
  const calls = [];
  const panel = panelWithActions(['edit'], calls);
  panel.startEdit('title', panel.data.title);
  panel.draft = '   ';
  panel.commitDraft('title');
  assert.equal(calls.length, 0);
});

test('toggleMulti adds and removes ids', () => {
  const calls = [];
  const panel = panelWithActions(['assign'], calls);
  panel.toggleMulti('assignees', '9', true);
  assert.deepStrictEqual(Array.from(calls[0][1].assignees), ['7', '9']);
  panel.toggleMulti('assignees', '7', false);
  assert.deepStrictEqual(Array.from(calls[1][1].assignees), []);
});

test('toggleMulti is gated on the matching action', () => {
  const calls = [];
  panelWithActions([], calls).toggleMulti('assignees', '9', true);
  assert.equal(calls.length, 0);
});

test('taskPanel assignee helpers expose names and the unassigned list', () => {
  const panel = panelWithActions(['assign'], []);
  panel.users = [
    { id: '7', username: 'alice' },
    { id: '9', username: 'bob' },
  ];
  panel.assigneeNames = { 7: 'alice', 9: 'bob' };
  assert.equal(panel.assigneeName('9'), 'bob');
  assert.equal(panel.assigneeName('missing'), 'Unknown user');
  assert.deepStrictEqual(
    Array.from(panel.unassignedUsers()).map((u) => u.id),
    ['9']
  );
});

test('addAssignee and removeAssignee patch through toggleMulti', () => {
  const calls = [];
  const panel = panelWithActions(['assign'], calls);
  panel.addAssignee({ id: '9', username: 'bob' });
  assert.deepStrictEqual(Array.from(calls[0][1].assignees), ['7', '9']);
  panel.removeAssignee('7');
  assert.deepStrictEqual(Array.from(calls[1][1].assignees), []);
});

test('commitField is gated on the matching action', () => {
  const calls = [];
  const panel = panelWithActions(['edit'], calls);
  panel.commitField('status', 's2');
  assert.equal(calls.length, 0);
  panel.commitField('priority', 'high');
  assert.equal(calls.length, 1);
});

test('removeTask is gated on the delete action', () => {
  const blocked = [];
  panelWithActions([], blocked).removeTask();
  assert.equal(blocked.length, 0);

  const calls = [];
  panelWithActions(['delete'], calls).removeTask();
  assert.deepStrictEqual(Array.from(calls[0]), ['delete', 'u1', 'Task one']);
});

test('projectBoard form assignee helpers add, dedupe and remove', () => {
  const board = panelBoard();
  board.members = [
    { id: '1', username: 'alice' },
    { id: '2', username: 'bob' },
  ];
  board.form.assignees = ['1'];
  assert.deepStrictEqual(
    Array.from(board.formUnassignedUsers()).map((u) => u.id),
    ['2']
  );
  board.addFormAssignee({ id: '2', username: 'bob' });
  board.addFormAssignee({ id: '2', username: 'bob' });
  assert.deepStrictEqual(Array.from(board.form.assignees), ['1', '2']);
  board.removeFormAssignee('1');
  assert.deepStrictEqual(Array.from(board.form.assignees), ['2']);
  assert.equal(board.formAssigneeName('2'), 'bob');
  assert.equal(board.formAssigneeName('missing'), 'Unknown user');
});

test('openTask pushes the task URL and swaps the panel', async () => {
  ctx.getCSRFToken = () => 'token';
  ctx.localStorage = { getItem: () => null, setItem: () => {} };
  const calls = [];
  ctx.location = { href: 'http://x.test/projects/p/board' };
  ctx.history = {
    pushState: (s, t, url) => calls.push('push ' + url),
    replaceState: (s, t, url) => calls.push('replace ' + url),
  };
  const board = ctx.projectBoard({
    apiBase: '/api',
    projectBase: '/projects/p',
    writable: true,
  });
  board.$ajax = (url, opts) => calls.push('ajax ' + url + ' -> ' + opts.target);
  await board.openTask('u1');
  assert.equal(board.panelTaskUuid, 'u1');
  assert.deepStrictEqual(Array.from(calls), [
    'push /projects/p/board?task=u1',
    'ajax /projects/p/tasks/u1/panel -> task-panel',
  ]);
});

test('closePanel clears state and strips the param', () => {
  ctx.getCSRFToken = () => 'token';
  ctx.localStorage = { getItem: () => null, setItem: () => {} };
  const calls = [];
  ctx.location = { href: 'http://x.test/projects/p/board?task=u1' };
  ctx.history = {
    pushState: (s, t, url) => calls.push('push ' + url),
    replaceState: (s, t, url) => calls.push('replace ' + url),
  };
  const board = ctx.projectBoard({
    apiBase: '/api',
    projectBase: '/projects/p',
    writable: true,
  });
  board.panelTaskUuid = 'u1';
  board.closePanel();
  assert.equal(board.panelTaskUuid, null);
  assert.deepStrictEqual(Array.from(calls), ['replace /projects/p/board']);
});

function panelBoard() {
  ctx.getCSRFToken = () => 'token';
  ctx.localStorage = { getItem: () => null, setItem: () => {} };
  ctx.location = { href: 'http://x.test/projects/p/board?task=u1' };
  ctx.history = { pushState: () => {}, replaceState: () => {} };
  return ctx.projectBoard({
    apiBase: '/api',
    projectBase: '/projects/p',
    writable: true,
  });
}

test('a failed panel load alerts and closes the panel', async () => {
  const alerts = [];
  ctx.AppAlert = { error: (message) => alerts.push(message) };
  const board = panelBoard();
  board.$ajax = () => Promise.reject(new Error('boom'));
  board.panelTaskUuid = 'u1';
  await board._loadPanel('u1');
  assert.equal(board.panelTaskUuid, null);
  assert.deepStrictEqual(Array.from(alerts), ['Could not load the task.']);
});

test('a stale panel load failure leaves the newer panel alone', async () => {
  const alerts = [];
  ctx.AppAlert = { error: (message) => alerts.push(message) };
  const board = panelBoard();
  board.$ajax = (url) =>
    url.includes('u1') ? Promise.reject(new Error('boom')) : Promise.resolve();
  board.panelTaskUuid = 'u1';
  const stale = board._loadPanel('u1');
  board.panelTaskUuid = 'u2';
  await board._loadPanel('u2');
  await stale;
  assert.equal(board.panelTaskUuid, 'u2');
  assert.deepStrictEqual(Array.from(alerts), []);
});

test('patchTask re-renders server truth through refresh', async () => {
  const calls = [];
  ctx.fetch = async (url, opts) => {
    calls.push(opts.method + ' ' + url);
    return { ok: true };
  };
  const board = panelBoard();
  board.refresh = () => calls.push('refresh');
  board.panelTaskUuid = 'u1';
  await board.patchTask('u1', { title: 'Renamed' });
  assert.deepStrictEqual(Array.from(calls), ['PATCH /api/tasks/u1', 'refresh']);
});

test('refresh reloads the open panel alongside the board', () => {
  const calls = [];
  const board = panelBoard();
  board.$ajax = (url, opts) => calls.push('ajax ' + url + ' -> ' + opts.target);
  board._loadPanel = (uuid) => calls.push('load ' + uuid);
  board.currentView = 'board';
  board.panelTaskUuid = 'u1';
  board.refresh();
  assert.deepStrictEqual(Array.from(calls), [
    'ajax /projects/p/board -> project-content',
    'load u1',
  ]);
});

test('refresh leaves the panel alone when none is open', () => {
  const calls = [];
  const board = panelBoard();
  board.$ajax = (url, opts) => calls.push('ajax ' + url + ' -> ' + opts.target);
  board._loadPanel = (uuid) => calls.push('load ' + uuid);
  board.currentView = 'board';
  board.panelTaskUuid = null;
  board.refresh();
  assert.deepStrictEqual(Array.from(calls), [
    'ajax /projects/p/board -> project-content',
  ]);
});

test('refresh targets the settings view when active', () => {
  const calls = [];
  const board = panelBoard();
  board.$ajax = (url, opts) => calls.push('ajax ' + url + ' -> ' + opts.target);
  board._loadPanel = () => {};
  board.currentView = 'settings';
  board.panelTaskUuid = null;
  board.refresh();
  assert.deepStrictEqual(Array.from(calls), [
    'ajax /projects/p/settings -> project-content',
  ]);
});

test('copyLink writes the task deep link and flashes feedback', async () => {
  const written = [];
  ctx.location = {
    href: 'http://x.test/projects/p/board',
    pathname: '/projects/p/board',
    search: '',
    origin: 'http://x.test',
  };
  ctx.navigator = { clipboard: { writeText: async (url) => written.push(url) } };
  ctx.setTimeout = () => {};
  const panel = panelWithActions([], []);
  panel.copyLink('WR-3');
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepStrictEqual(written, ['http://x.test/projects/p/board?task=WR-3']);
  assert.equal(panel.linkCopied, true);
});

test('copyLink still copies when the URL already targets the task', async () => {
  const written = [];
  ctx.location = {
    href: 'http://x.test/projects/p/board?task=WR-3',
    pathname: '/projects/p/board',
    search: '?task=WR-3',
    origin: 'http://x.test',
  };
  ctx.navigator = { clipboard: { writeText: async (url) => written.push(url) } };
  ctx.setTimeout = () => {};
  const panel = panelWithActions([], []);
  panel.copyLink('WR-3');
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepStrictEqual(written, ['http://x.test/projects/p/board?task=WR-3']);
});

function selectorLabels() {
  return [
    { uuid: 'l1', name: 'Bug', color: '#ef4444' },
    { uuid: 'l2', name: 'Feature', color: '#3b82f6' },
    { uuid: 'l3', name: 'Backend', color: '' },
  ];
}

function labelPicker(overrides) {
  const opts = {
    all: selectorLabels,
    selected: () => [],
    createUrl: '',
    ...overrides,
  };
  return ctx.labelSelector('label-picked', opts.all, opts.selected, opts.createUrl);
}

function keyEvent(key) {
  return {
    key: key,
    prevented: false,
    preventDefault() {
      this.prevented = true;
    },
  };
}

test('labelSelector filters case-insensitively over unselected labels', () => {
  const sel = labelPicker({ selected: () => ['l2'] });
  sel.query = 'b';
  sel.searchLocal();
  assert.deepStrictEqual(
    Array.from(sel.results).map((l) => l.uuid),
    ['l1', 'l3']
  );
  sel.query = 'FEAT';
  sel.searchLocal();
  assert.deepStrictEqual(Array.from(sel.results), []);
});

test('showCreate needs a create URL, a query, and no exact match', () => {
  const noUrl = labelPicker({});
  noUrl.query = 'urgent';
  assert.equal(noUrl.showCreate(), false);

  const sel = labelPicker({ createUrl: '/api/labels' });
  sel.query = '';
  assert.equal(sel.showCreate(), false);
  sel.query = '  urgent ';
  assert.equal(sel.showCreate(), true);
  sel.query = 'BUG';
  assert.equal(sel.showCreate(), false);
});

test('showCreate stays hidden when the exact match is already selected', () => {
  const sel = labelPicker({ createUrl: '/api/labels', selected: () => ['l1'] });
  sel.query = 'bug';
  assert.equal(sel.showCreate(), false);
});

test('pickLabelColor picks the least-used palette color', () => {
  const pick = ctx.projectBoardHelpers.pickLabelColor;
  assert.equal(pick([]), '#ef4444');
  assert.equal(
    pick([{ color: '#ef4444' }, { color: '' }, { color: 'not-in-palette' }]),
    '#f97316'
  );
  assert.equal(
    pick([
      { color: '#ef4444' },
      { color: '#f97316' },
      { color: '#eab308' },
      { color: '#22c55e' },
      { color: '#3b82f6' },
      { color: '#a855f7' },
      { color: '#f97316' },
    ]),
    '#ef4444'
  );
});

test('createLabel posts name and auto color then announces and selects', async () => {
  const calls = [];
  const events = [];
  ctx.getCSRFToken = () => 'token';
  ctx.CustomEvent = function (name, opts) {
    return { name: name, detail: opts && opts.detail };
  };
  ctx.dispatchEvent = (e) => events.push(e);
  ctx.fetch = async (url, opts) => {
    calls.push(opts.method + ' ' + url + ' ' + opts.body);
    return {
      ok: true,
      json: async () => ({ uuid: 'l9', name: 'Urgent', color: '#f97316' }),
    };
  };
  const sel = labelPicker({ createUrl: '/api/labels' });
  sel.query = ' Urgent ';
  await sel.createLabel();
  // Fixture colors: #ef4444 and #3b82f6 used once, so #f97316 is least used.
  assert.deepStrictEqual(Array.from(calls), [
    'POST /api/labels {"name":"Urgent","color":"#f97316"}',
  ]);
  assert.deepStrictEqual(
    Array.from(events).map((e) => e.name),
    ['project-label-created', 'label-picked']
  );
  assert.deepStrictEqual(
    { ...events[0].detail.label },
    { uuid: 'l9', name: 'Urgent', color: '#f97316' }
  );
  assert.equal(sel.query, '');
  assert.equal(sel.creating, false);
  assert.equal(sel.createError, false);
});

test('createLabel keeps the query and flags the error on failure', async () => {
  ctx.getCSRFToken = () => 'token';
  ctx.fetch = async () => ({ ok: false });
  const sel = labelPicker({ createUrl: '/api/labels' });
  sel.query = 'Urgent';
  await sel.createLabel();
  assert.equal(sel.createError, true);
  assert.equal(sel.query, 'Urgent');
  assert.equal(sel.creating, false);
});

test('Enter selects the exact match instead of creating', () => {
  const events = [];
  ctx.CustomEvent = function (name, opts) {
    return { name: name, detail: opts && opts.detail };
  };
  ctx.dispatchEvent = (e) => events.push(e);
  const sel = labelPicker({ createUrl: '/api/labels' });
  sel.query = 'bug';
  sel.searchLocal();
  const e = keyEvent('Enter');
  sel.handleKeydown(e);
  assert.equal(e.prevented, true);
  assert.deepStrictEqual(
    Array.from(events).map((ev) => ev.name),
    ['label-picked']
  );
  assert.equal(events[0].detail.label.uuid, 'l1');
});

test('Enter without a query falls through to the surrounding form', () => {
  const sel = labelPicker({});
  const e = keyEvent('Enter');
  sel.handleKeydown(e);
  assert.equal(e.prevented, false);
});

test('Enter with a query is swallowed but selects nothing once the dropdown is closed', () => {
  const events = [];
  ctx.CustomEvent = function (name, opts) {
    return { name: name, detail: opts && opts.detail };
  };
  ctx.dispatchEvent = (e) => events.push(e);
  ctx.fetch = async () => {
    throw new Error('createLabel must not run while the dropdown is closed');
  };
  const sel = labelPicker({ createUrl: '/api/labels' });
  sel.query = 'bug';
  sel.searchLocal();
  sel.showDropdown = false;
  const e = keyEvent('Enter');
  sel.handleKeydown(e);
  assert.equal(e.prevented, true);
  assert.deepStrictEqual(Array.from(events), []);
});

test('Enter with no highlight and no exact match creates the label', async () => {
  const calls = [];
  const events = [];
  ctx.getCSRFToken = () => 'token';
  ctx.CustomEvent = function (name, opts) {
    return { name: name, detail: opts && opts.detail };
  };
  ctx.dispatchEvent = (e) => events.push(e);
  ctx.fetch = async (url, opts) => {
    calls.push(opts.method + ' ' + url + ' ' + opts.body);
    return {
      ok: true,
      json: async () => ({ uuid: 'l9', name: 'Urgent', color: '#f97316' }),
    };
  };
  const sel = labelPicker({ createUrl: '/api/labels' });
  sel.query = 'Urgent';
  sel.searchLocal();
  const e = keyEvent('Enter');
  sel.handleKeydown(e);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(e.prevented, true);
  assert.deepStrictEqual(Array.from(calls), [
    'POST /api/labels {"name":"Urgent","color":"#f97316"}',
  ]);
  assert.deepStrictEqual(
    Array.from(events).map((ev) => ev.name),
    ['project-label-created', 'label-picked']
  );
});

test('arrow keys wrap across the results plus the create row', () => {
  const sel = labelPicker({ createUrl: '/api/labels' });
  sel.query = 'b';
  sel.searchLocal();
  // Results: l1 (Bug), l3 (Backend); "b" has no exact match so the create
  // row is item index 2.
  sel.handleKeydown(keyEvent('ArrowDown'));
  assert.equal(sel.highlight, 0);
  sel.handleKeydown(keyEvent('ArrowDown'));
  sel.handleKeydown(keyEvent('ArrowDown'));
  assert.equal(sel.highlight, 2);
  sel.handleKeydown(keyEvent('ArrowDown'));
  assert.equal(sel.highlight, 0);
  sel.handleKeydown(keyEvent('ArrowUp'));
  assert.equal(sel.highlight, 2);
});

test('projectBoard label helpers resolve names, styles and creations', () => {
  const board = panelBoard();
  board.labels = [
    { uuid: 'l1', name: 'Bug', color: '#ef4444' },
    { uuid: 'l2', name: 'Chore', color: '' },
  ];
  assert.equal(board.labelName('l1'), 'Bug');
  assert.equal(board.labelName('missing'), 'Unknown label');
  assert.equal(board.labelStyle('l1'), 'border-color: #ef4444; color: #ef4444');
  assert.equal(board.labelStyle('l2'), '');
  assert.equal(board.labelStyle('missing'), '');
  board.onLabelCreated({ uuid: 'l3', name: 'New', color: '' });
  board.onLabelCreated({ uuid: 'l3', name: 'New', color: '' });
  assert.equal(board.labels.length, 3);
});

test('projectBoard form label helpers add, dedupe and remove', () => {
  const board = panelBoard();
  board.form.labels = ['l1'];
  board.addFormLabel({ uuid: 'l2', name: 'Chore', color: '' });
  board.addFormLabel({ uuid: 'l2', name: 'Chore', color: '' });
  assert.deepStrictEqual(Array.from(board.form.labels), ['l1', 'l2']);
  board.removeFormLabel('l1');
  assert.deepStrictEqual(Array.from(board.form.labels), ['l2']);
});

test('panel addLabel and removeLabel patch through toggleMulti', () => {
  const calls = [];
  const panel = panelWithActions(['set_labels'], calls);
  panel.data.labels = ['l1'];
  panel.addLabel({ uuid: 'l2', name: 'Chore', color: '' });
  assert.deepStrictEqual(Array.from(calls[0][1].labels), ['l1', 'l2']);
  panel.removeLabel('l1');
  assert.deepStrictEqual(Array.from(calls[1][1].labels), []);
});

test('panel addLabel is gated on the set_labels action', () => {
  const calls = [];
  panelWithActions([], calls).addLabel({ uuid: 'l2', name: 'Chore', color: '' });
  assert.equal(calls.length, 0);
});

test('taskMatchesFilters filters by status', () => {
  const dataset = { search: 'wr-1 fix login', priority: 'high', status: 's1' };
  assert.equal(
    ctx.projectBoardHelpers.taskMatchesFilters(dataset, { status: 's1' }),
    true
  );
  assert.equal(
    ctx.projectBoardHelpers.taskMatchesFilters(dataset, { status: 's2' }),
    false
  );
});

test('taskMatchesFilters ignores an empty status filter', () => {
  const dataset = { search: 'wr-1 fix login', status: 's1' };
  assert.equal(ctx.projectBoardHelpers.taskMatchesFilters(dataset, { status: '' }), true);
});

test('taskMatchesFilters skips rows without status metadata', () => {
  const dataset = { search: 'wr-1 fix login' };
  assert.equal(
    ctx.projectBoardHelpers.taskMatchesFilters(dataset, { status: 's1' }),
    true
  );
});

test('refresh targets the all-tasks partial when viewing tasks', () => {
  ctx.localStorage = { getItem: () => null, setItem: () => {} };
  const calls = [];
  const board = ctx.projectBoard({
    apiBase: '/api',
    projectBase: '/projects/p',
    writable: true,
  });
  board.currentView = 'tasks';
  board.$ajax = (url) => calls.push(url);
  board.refresh();
  assert.deepStrictEqual(Array.from(calls), ['/projects/p/tasks']);
});

test('onPopState recognizes the tasks view', () => {
  ctx.localStorage = { getItem: () => null, setItem: () => {} };
  ctx.location = {
    pathname: '/projects/p/tasks',
    href: 'http://x.test/projects/p/tasks',
  };
  const board = ctx.projectBoard({
    apiBase: '/api',
    projectBase: '/projects/p',
    writable: true,
  });
  board.onPopState();
  assert.equal(board.currentView, 'tasks');
});

function keydownBoard() {
  ctx.localStorage = { getItem: () => null, setItem: () => {} };
  return ctx.projectBoard({
    apiBase: '/api',
    projectBase: '/projects/p',
    writable: true,
  });
}

function helpKeyEvent(overrides) {
  return Object.assign(
    { key: '?', target: { tagName: 'DIV' }, preventDefault: () => {} },
    overrides
  );
}

test('handleKeydown opens the help dialog on "?"', () => {
  const shown = [];
  ctx.document = {
    querySelector: () => null,
    getElementById: (id) => ({ showModal: () => shown.push(id) }),
  };
  keydownBoard().handleKeydown(helpKeyEvent({}));
  assert.deepStrictEqual(shown, ['projects-help-dialog']);
});

test('handleKeydown ignores "?" while typing or with a dialog open', () => {
  const shown = [];
  ctx.document = {
    querySelector: () => null,
    getElementById: (id) => ({ showModal: () => shown.push(id) }),
  };
  const board = keydownBoard();
  board.handleKeydown(helpKeyEvent({ target: { tagName: 'INPUT' } }));
  board.handleKeydown(helpKeyEvent({ target: { tagName: 'DIV', isContentEditable: true } }));
  board.handleKeydown(helpKeyEvent({ ctrlKey: true }));
  ctx.document.querySelector = () => ({ open: true });
  board.handleKeydown(helpKeyEvent({}));
  assert.deepStrictEqual(shown, []);
});
