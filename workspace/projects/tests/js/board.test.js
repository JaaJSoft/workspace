const assert = require('node:assert');
const { test } = require('node:test');

const {
  loadScripts,
  CUSTOM_ELEMENT_STUBS,
} = require('../../../common/tests/js/loader');

// board.js reads the shared <tag-chip> palette, which base.html loads
// before it.
const ctx = loadScripts(
  [
    'workspace/common/static/ui/js/tag_chip.js',
    'workspace/common/static/ui/js/attachment_input.js',
    'workspace/projects/ui/static/projects/ui/js/board.js',
  ],
  { ...CUSTOM_ELEMENT_STUBS, URL }
);

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

test('taskFiltersFromUrl reads single and repeated filter params', () => {
  const filters = ctx.projectBoardHelpers.taskFiltersFromUrl(
    'http://x.test/projects/p/board?q=bug&assignee=none&assignee=7&task=u1&label=l1'
  );
  assert.equal(filters.q, 'bug');
  assert.deepStrictEqual(Array.from(filters.assignee), ['none', '7']);
  assert.deepStrictEqual(Array.from(filters.label), ['l1']);
  assert.equal(filters.priority, '');
  assert.equal(filters.status, '');
});

test('taskFilterUrl writes the filters and preserves the task param', () => {
  const url = ctx.projectBoardHelpers.taskFilterUrl(
    'http://x.test/projects/p/board?task=u1&priority=low',
    { q: ' bug ', assignee: ['none', '7'], label: [], priority: 'high', status: '' }
  );
  assert.equal(
    url,
    '/projects/p/board?task=u1&priority=high&q=bug&assignee=none&assignee=7'
  );
});

test('taskFilterUrl drops every filter param when the filters are empty', () => {
  assert.equal(
    ctx.projectBoardHelpers.taskFilterUrl(
      'http://x.test/projects/p/board?q=bug&priority=high&assignee=7&task=u1',
      { q: '', assignee: [], label: [], priority: '', status: '' }
    ),
    '/projects/p/board?task=u1'
  );
});

test('filtersActive and clearFilters track filter state', () => {
  const board = panelBoard();
  const fetched = [];
  board.$ajax = (url) => fetched.push(url);
  assert.equal(board.filtersActive(), false);
  board.filters.q = '   ';
  assert.equal(board.filtersActive(), false);
  board.filters.q = 'bug';
  assert.equal(board.filtersActive(), true);
  board.clearFilters();
  assert.equal(board.filtersActive(), false);
  assert.deepStrictEqual(Array.from(fetched), ['/projects/p/board?task=u1']);
  board.filters.assignee = ['none'];
  assert.equal(board.filtersActive(), true);
});

test('assignee filter toggles, chips exclude the none pseudo-user', () => {
  const board = panelBoard();
  const fetched = [];
  board.$ajax = (url) => fetched.push(url);
  board.members = [{ id: '7', username: 'alice' }];
  board.toggleAssigneeFilter('none');
  board.addAssigneeFilter({ id: '7', username: 'alice' });
  board.addAssigneeFilter({ id: '7', username: 'alice' }); // no duplicate
  assert.deepStrictEqual(Array.from(board.filters.assignee), ['none', '7']);
  assert.deepStrictEqual(Array.from(board.assigneeFilterChips()), ['7']);
  assert.equal(board.filterAssigneeName('7'), 'alice');
  assert.deepStrictEqual(Array.from(board.unfilteredMembers()), []);
  assert.equal(
    fetched[fetched.length - 1],
    '/projects/p/board?task=u1&assignee=none&assignee=7'
  );
  board.removeAssigneeFilter('7');
  assert.deepStrictEqual(Array.from(board.filters.assignee), ['none']);
});

test('label filter adds once and removes', () => {
  const board = panelBoard();
  const fetched = [];
  board.$ajax = (url) => fetched.push(url);
  board.addLabelFilter({ uuid: 'l1' });
  board.addLabelFilter({ uuid: 'l1' });
  board.addLabelFilter({ uuid: 'l2' });
  assert.deepStrictEqual(Array.from(board.filters.label), ['l1', 'l2']);
  assert.equal(fetched.length, 2);
  board.removeLabelFilter('l1');
  assert.deepStrictEqual(Array.from(board.filters.label), ['l2']);
  assert.equal(
    fetched[fetched.length - 1],
    '/projects/p/board?task=u1&label=l2'
  );
});

test('activeFilterCount counts panel filters and ignores the search box', () => {
  const board = panelBoard();
  assert.equal(board.activeFilterCount(), 0);
  board.filters.q = 'bug';
  assert.equal(board.activeFilterCount(), 0);
  board.filters.assignee = ['none', '7'];
  board.filters.label = ['l1'];
  board.filters.priority = 'high';
  board.filters.status = 's1';
  assert.equal(board.activeFilterCount(), 5);
});

test('priority and status filter chips resolve their display name and color', () => {
  const board = panelBoard();
  board.statuses = [{ uuid: 's1', name: 'Doing', color: '#123456' }];
  board.filters.priority = 'high';
  board.filters.status = 's1';
  assert.equal(board.filterPriorityName(), 'High');
  assert.equal(board.filterStatusName(), 'Doing');
  assert.equal(board.filterStatusColor(), '#123456');
  board.filters.status = 'missing';
  assert.equal(board.filterStatusName(), 'Unknown status');
  assert.equal(board.filterStatusColor(), '');
});

test('applyFilters rewrites the URL and refetches the task list only', () => {
  const board = panelBoard();
  const fetched = [];
  const replaced = [];
  ctx.history.replaceState = (a, b, url) => replaced.push(url);
  board.$ajax = (url, opts) => fetched.push([url, opts.target]);
  board.filters.priority = 'high';
  board.applyFilters();
  assert.deepStrictEqual(Array.from(replaced), [
    '/projects/p/board?task=u1&priority=high',
  ]);
  // 'task-collection', not 'project-content': swapping the filter bar
  // along with the list would rebuild the open filters popover.
  assert.deepStrictEqual(Array.from(fetched.map((f) => Array.from(f))), [
    ['/projects/p/board?task=u1&priority=high', 'task-collection'],
  ]);
});

test('onDragStart is cancelled while filters are active', () => {
  const board = panelBoard();
  let prevented = false;
  board.filters.priority = 'high';
  board.onDragStart({ preventDefault: () => (prevented = true) }, 'u1');
  assert.equal(prevented, true);
  assert.equal(board.dragging, null);
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

function backlogDom(uuids) {
  return {
    querySelectorAll: (selector) => {
      assert.equal(selector, '#backlog [data-task-uuid]');
      return uuids.map((uuid) => ({ dataset: { taskUuid: uuid } }));
    },
  };
}

test('toggleSelectAll selects every rendered row', () => {
  ctx.document = backlogDom(['u1', 'u3']);
  const board = panelBoard();
  board.toggleSelectAll();
  assert.deepStrictEqual(Array.from(board.selected), ['u1', 'u3']);
  assert.equal(board.allVisibleSelected(), true);
  board.toggleSelectAll();
  assert.deepStrictEqual(Array.from(board.selected), []);
});

test('toggleSelectAll keeps selections made under another filter', () => {
  // u2 was selected before a server-side filter removed its row from the
  // DOM; select-all/deselect-all here must not touch it.
  ctx.document = backlogDom(['u1', 'u3']);
  const board = panelBoard();
  board.selected = ['u2'];
  board.toggleSelectAll();
  assert.deepStrictEqual(Array.from(board.selected), ['u2', 'u1', 'u3']);
  board.toggleSelectAll();
  assert.deepStrictEqual(Array.from(board.selected), ['u2']);
});

test('toggleSelectAll leaves the selection alone when nothing is rendered', () => {
  ctx.document = backlogDom([]);
  const board = panelBoard();
  board.selected = ['u1'];
  board.toggleSelectAll();
  assert.deepStrictEqual(Array.from(board.selected), ['u1']);
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
    estimate: 'edit',
    status: 'move',
    due_date: 'set_due',
    assignees: 'assign',
    labels: 'set_labels',
    epic: 'set_epic',
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

test('sectionDefaults opens filled sections and collapses empty ones', () => {
  const panel = ctx.taskPanel();
  panel.data = { assignees: [], labels: ['l1'], description: '   ' };
  panel.subtasks = [{ uuid: 's1', title: 'a', done: false }];
  panel.links = [];
  panel.attachments = [];
  panel._commentCount = 2;
  panel._activityCount = 0;
  assert.deepStrictEqual(
    { ...panel.sectionDefaults() },
    {
      assignees: false,
      labels: true,
      description: false,
      checklist: true,
      links: false,
      attachments: false,
      comments: true,
      activity: false,
    }
  );
  panel.sectionsOpen = panel.sectionDefaults();
  panel.toggleSection('links');
  assert.equal(panel.sectionsOpen.links, true);
  panel.toggleSection('labels');
  assert.equal(panel.sectionsOpen.labels, false);
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
  ctx.location = {
    origin: 'http://x.test',
    href: 'http://x.test/projects/p/board?task=u1',
  };
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
  // The palette is the shared <tag-chip> one, minus its "no color" entry.
  const palette = ctx.TAG_CHIP_COLORS.map((c) => c.value).filter(Boolean);

  assert.equal(pick([]), palette[0]);
  assert.equal(
    pick([{ color: palette[0] }, { color: '' }, { color: 'not-in-palette' }]),
    palette[1]
  );
  // Every color used once except the last: that one wins.
  assert.equal(
    pick(palette.slice(0, -1).map((color) => ({ color }))),
    palette[palette.length - 1]
  );
  // All used once, one used twice: the tie is broken by palette order.
  assert.equal(
    pick([...palette.map((color) => ({ color })), { color: palette[0] }]),
    palette[1]
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

test('projectBoard label helpers resolve names, colors and creations', () => {
  const board = panelBoard();
  board.labels = [
    { uuid: 'l1', name: 'Bug', color: '#ef4444' },
    { uuid: 'l2', name: 'Chore', color: '' },
  ];
  assert.equal(board.labelName('l1'), 'Bug');
  assert.equal(board.labelName('missing'), 'Unknown label');
  // <tag-chip> takes the color itself and turns it into the pill styling.
  assert.equal(board.labelColor('l1'), '#ef4444');
  assert.equal(board.labelColor('l2'), '');
  assert.equal(board.labelColor('missing'), '');
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

test('refresh targets the all-tasks partial when viewing tasks', () => {
  ctx.localStorage = { getItem: () => null, setItem: () => {} };
  ctx.location = {
    origin: 'http://x.test',
    href: 'http://x.test/projects/p/tasks',
  };
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

test('refresh targets the analytics partial when viewing analytics', () => {
  ctx.localStorage = { getItem: () => null, setItem: () => {} };
  ctx.location = {
    origin: 'http://x.test',
    href: 'http://x.test/projects/p/analytics',
  };
  const calls = [];
  const board = ctx.projectBoard({
    apiBase: '/api',
    projectBase: '/projects/p',
    writable: true,
  });
  board.currentView = 'analytics';
  board.$ajax = (url) => calls.push(url);
  board.refresh();
  assert.deepStrictEqual(Array.from(calls), ['/projects/p/analytics']);
});

test('onPopState recognizes the analytics view', () => {
  ctx.localStorage = { getItem: () => null, setItem: () => {} };
  ctx.location = {
    pathname: '/projects/p/analytics',
    href: 'http://x.test/projects/p/analytics',
  };
  const board = ctx.projectBoard({
    apiBase: '/api',
    projectBase: '/projects/p',
    writable: true,
  });
  board.onPopState();
  assert.equal(board.currentView, 'analytics');
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

test('searchLinkTasks: clearing the input invalidates an in-flight search', async () => {
  const panel = ctx.taskPanel();
  panel.data = { uuid: 'anchor-uuid', link_search_url: '/api/v1/projects/tasks/search' };
  panel.links = [];

  let resolveFetch;
  ctx.fetch = () => new Promise((resolve) => { resolveFetch = resolve; });

  panel.linkQuery = 'exp';
  const pending = panel.searchLinkTasks();

  // The user clears the input before the response lands.
  panel.linkQuery = '';
  await panel.searchLinkTasks();
  assert.equal(panel.linkDropdown, false);

  resolveFetch({
    ok: true,
    json: async () => [{ uuid: 'other', reference: 'IT-1', title: 'Stale' }],
  });
  await pending;

  // The stale response must not reopen the dropdown nor restore results.
  assert.equal(panel.linkDropdown, false);
  assert.equal(panel.linkResults.length, 0);
});

test('taskFiltersFromUrl and taskFilterUrl round-trip the epic filter', () => {
  const filters = ctx.projectBoardHelpers.taskFiltersFromUrl(
    'http://x.test/projects/p/board?epic=e1&epic=e2&task=u1'
  );
  assert.deepStrictEqual(Array.from(filters.epic), ['e1', 'e2']);
  assert.equal(
    ctx.projectBoardHelpers.taskFilterUrl(
      'http://x.test/projects/p/board?task=u1',
      { ...filters }
    ),
    '/projects/p/board?task=u1&epic=e1&epic=e2'
  );
});

test('epic filter adds once, removes, and counts as active', () => {
  const board = panelBoard();
  const fetched = [];
  board.$ajax = (url) => fetched.push(url);
  assert.equal(board.filtersActive(), false);
  board.addEpicFilter({ uuid: 'e1' });
  board.addEpicFilter({ uuid: 'e1' });
  assert.deepStrictEqual(Array.from(board.filters.epic), ['e1']);
  assert.equal(fetched.length, 1);
  assert.equal(board.filtersActive(), true);
  assert.equal(board.activeFilterCount(), 1);
  board.removeEpicFilter('e1');
  assert.deepStrictEqual(Array.from(board.filters.epic), []);
  assert.equal(board.filtersActive(), false);
});

test('epic lookups resolve names and colors, openEpics drops closed ones', () => {
  const board = panelBoard();
  board.epics = [
    { uuid: 'e1', name: 'Launch', color: '#3b82f6', closed: false },
    { uuid: 'e2', name: 'Done era', color: '', closed: true },
  ];
  assert.equal(board.epicName('e1'), 'Launch');
  assert.equal(board.epicColor('e1'), '#3b82f6');
  assert.equal(board.epicName('missing'), 'Unknown epic');
  assert.equal(board.epicColor('e2'), '');
  assert.deepStrictEqual(
    Array.from(board.openEpics()).map((e) => e.uuid),
    ['e1']
  );
});

test('createEpic posts name and auto color then joins the shared list', async () => {
  const board = panelBoard();
  board.epics = [{ uuid: 'e1', name: 'Launch', color: '#ef4444', closed: false }];
  const calls = [];
  ctx.fetch = async (url, opts) => {
    calls.push(opts.method + ' ' + url + ' ' + opts.body);
    return {
      ok: true,
      json: async () => ({ uuid: 'e9', name: 'Polish', color: '#3b82f6' }),
    };
  };
  const epic = await board.createEpic('  Polish ');
  assert.equal(epic.uuid, 'e9');
  // Fixture color #ef4444 is used once, so the least-used palette color wins.
  assert.deepStrictEqual(Array.from(calls), [
    'POST /api/epics {"name":"Polish","color":"#f97316"}',
  ]);
  assert.deepStrictEqual({ ...board.epics[1] }, {
    uuid: 'e9',
    name: 'Polish',
    color: '#3b82f6',
    closed: false,
  });
});

test('createEpic returns null and leaves the list alone on failure', async () => {
  const board = panelBoard();
  board.epics = [];
  ctx.fetch = async () => ({ ok: false });
  ctx.AppAlert = { error: () => {} };
  assert.equal(await board.createEpic('Nope'), null);
  assert.equal(await board.createEpic('   '), null);
  assert.equal(board.epics.length, 0);
});

test('the epic field commits through the set_epic action gate', () => {
  const calls = [];
  const panel = panelWithActions(['set_epic'], calls);
  panel.commitField('epic', 'e1');
  assert.deepStrictEqual([calls[0][0], { ...calls[0][1] }], ['u1', { epic: 'e1' }]);
  const gated = panelWithActions(['edit'], calls);
  gated.commitField('epic', null);
  assert.equal(calls.length, 1);
});

function sprintBoard(calls, islandData) {
  ctx.getCSRFToken = () => 'token';
  ctx.localStorage = { getItem: () => null, setItem: () => {} };
  ctx.location = { href: 'http://x.test/projects/p/board?sprint=s1' };
  ctx.history = {
    pushState: () => {},
    replaceState: (state, title, url) => calls.push('replace ' + url),
  };
  ctx.document = {
    getElementById: (id) =>
      id === 'board-sprint-data' && islandData
        ? { textContent: JSON.stringify(islandData) }
        : null,
  };
  const board = ctx.projectBoard({
    apiBase: '/api',
    projectBase: '/projects/p',
    writable: true,
  });
  board.refresh = () => calls.push('refresh');
  return board;
}

test('sendSelectedToSprint posts the selection and clears it', async () => {
  const calls = [];
  ctx.fetch = async (url, opts) => {
    calls.push(opts.method + ' ' + url + ' ' + opts.body);
    return { ok: true };
  };
  const board = sprintBoard(calls, null);
  board.selected = ['a', 'b'];
  await board.sendSelectedToSprint('s1');
  assert.equal(board.selected.length, 0);
  assert.deepStrictEqual(Array.from(calls), [
    'POST /api/tasks/assign-sprint {"sprint":"s1","tasks":["a","b"]}',
    'refresh',
  ]);
});

test('sendSelectedToSprint(null) clears the sprint assignment', async () => {
  const calls = [];
  ctx.fetch = async (url, opts) => {
    calls.push(opts.method + ' ' + url + ' ' + opts.body);
    return { ok: true };
  };
  const board = sprintBoard(calls, null);
  board.selected = ['a'];
  await board.sendSelectedToSprint(null);
  assert.deepStrictEqual(Array.from(calls), [
    'POST /api/tasks/assign-sprint {"sprint":null,"tasks":["a"]}',
    'refresh',
  ]);
});

test('startSprint confirms before posting', async () => {
  const calls = [];
  ctx.AppDialog = {
    confirm: async () => {
      calls.push('confirm');
      return true;
    },
  };
  ctx.fetch = async (url, opts) => {
    calls.push(opts.method + ' ' + url);
    return { ok: true, json: async () => ({}) };
  };
  const board = sprintBoard(calls, null);
  await board.startSprint('s1', 'Sprint 1');
  assert.deepStrictEqual(Array.from(calls), [
    'confirm',
    'POST /api/sprints/s1/start',
    'refresh',
  ]);
});

test('completeSprint offers backlog and planned sprints, backlog maps to null', async () => {
  const calls = [];
  ctx.AppDialog = {
    select: async (opts) => {
      calls.push('select:' + opts.options.map((o) => o.value).join(','));
      return 'backlog';
    },
  };
  ctx.fetch = async (url, opts) => {
    calls.push(opts.method + ' ' + url + ' ' + opts.body);
    return { ok: true, json: async () => ({}) };
  };
  const board = sprintBoard(calls, {
    uuid: 's1',
    name: 'Sprint 1',
    state: 'active',
    unfinished_count: 2,
    planned: [{ uuid: 's2', name: 'Sprint 2' }],
  });
  await board.completeSprint('s1', 'Sprint 1');
  assert.deepStrictEqual(Array.from(calls), [
    'select:backlog,s2',
    'POST /api/sprints/s1/complete {"move_to":null}',
    'replace /projects/p/board',
    'refresh',
  ]);
});

test('completeSprint aborts without a request when the dialog is cancelled', async () => {
  const calls = [];
  ctx.AppDialog = {
    select: async () => {
      calls.push('select');
      return null;
    },
  };
  ctx.fetch = async (url, opts) => {
    calls.push(opts.method + ' ' + url);
    return { ok: true, json: async () => ({}) };
  };
  const board = sprintBoard(calls, {
    uuid: 's1',
    name: 'Sprint 1',
    state: 'active',
    unfinished_count: 1,
    planned: [],
  });
  await board.completeSprint('s1', 'Sprint 1');
  assert.deepStrictEqual(Array.from(calls), ['select']);
});

test('dragging is blocked while the backlog is sprint-scoped', () => {
  const calls = [];
  const board = sprintBoard(calls, null);
  board.currentView = 'backlog';
  ctx.location = { href: 'http://x.test/projects/p/backlog?sprint=s1' };
  const event = {
    preventDefault: () => calls.push('prevented'),
    dataTransfer: { setData: () => {} },
  };
  board.onDragStart(event, 'u1');
  assert.equal(board.dragging, null);
  assert.deepStrictEqual(Array.from(calls), ['prevented']);
  // Dropping the scope re-enables dragging.
  ctx.location = { href: 'http://x.test/projects/p/backlog' };
  board.onDragStart(event, 'u1');
  assert.equal(board.dragging, 'u1');
});

test('refreshContent keeps the sprint scope on backlog refreshes', () => {
  const calls = [];
  const board = sprintBoard(calls, null);
  board.currentView = 'backlog';
  ctx.location = {
    href: 'http://x.test/projects/p/backlog?sprint=none',
    origin: 'http://x.test',
  };
  board.refresh = undefined;
  board.$ajax = (url, opts) => calls.push(url + ' -> ' + opts.target);
  board.refreshContent();
  assert.deepStrictEqual(Array.from(calls), [
    '/projects/p/backlog?sprint=none -> project-content',
  ]);
});
