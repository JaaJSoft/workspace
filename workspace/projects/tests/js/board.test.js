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
