// Live updates of the project pages: sse:projects.changed re-renders what is
// behind, never what already shows the change, and never under someone busy.
const assert = require('node:assert');
const { test, mock, beforeEach, afterEach } = require('node:test');

const {
  loadScripts,
  CUSTOM_ELEMENT_STUBS,
} = require('../../../common/tests/js/loader');
const { sidebarPreferenceStub } = require('../../../core/tests/js/sidebar_preference_stub');

const PROJECT = '11111111-1111-4111-8111-111111111111';
const OTHER_PROJECT = '22222222-2222-4222-8222-222222222222';

// Resolved at call time so the mocked timers of each test apply.
const ctx = loadScripts(
  [
    'workspace/common/static/ui/js/tag_chip.js',
    'workspace/common/static/ui/js/latest_only.js',
    'workspace/common/static/ui/js/attachment_input.js',
    'workspace/projects/ui/static/projects/ui/js/board.js',
  ],
  {
    ...CUSTOM_ELEMENT_STUBS,
    URL,
    URLSearchParams,
    setTimeout: (...args) => setTimeout(...args),
    clearTimeout: (...args) => clearTimeout(...args),
    sidebarPreference: sidebarPreferenceStub(),
  }
);

function fakeElement(id, revision, extra = {}) {
  return {
    id,
    isConnected: true,
    dataset: { projectRevision: String(revision) },
    scrollTop: 0,
    scrollLeft: 0,
    attributes: {},
    getAttribute(name) {
      return this.attributes[name] ?? null;
    },
    contains: (el) => !!el && el.inside === id,
    querySelectorAll: () => [],
    ...extra,
  };
}

// A focused element of the page; `inside` names the fragment holding it.
function focused(inside, { field = false } = {}) {
  return {
    inside,
    matches: () => field,
    closest: () => null,
  };
}

let dom;
// Drafts by region id, read through hasDraft() as projectBoard.resync does.
let drafts;
// Alpine effects registered to wait for a draft to go; re-run by hand.
let effects;

function makeBoard({ content = 10, panel = 10, view = 'board' } = {}) {
  dom = {
    content: fakeElement('project-content', content),
    panel: fakeElement('task-panel', panel),
  };
  drafts = { 'project-content': false, 'task-panel': false };
  effects = [];
  ctx.queueMicrotask = queueMicrotask;
  ctx.Alpine = {
    $data: (el) => ({ hasDraft: () => drafts[el.id] }),
    effect: (fn) => { effects.push(fn); fn(); return fn; },
    release: () => {},
  };
  ctx.document = {
    hidden: false,
    activeElement: null,
    getElementById: (id) =>
      ({ 'project-content': dom.content, 'task-panel': dom.panel })[id] || null,
  };
  ctx.location = {
    origin: 'http://x.test',
    href: 'http://x.test/projects/p/' + view,
  };
  const board = ctx.projectBoard({
    apiBase: '/api',
    projectBase: '/projects/p',
    projectUuid: PROJECT,
    writable: true,
    view,
  });
  board.requests = [];
  board.$ajax = (url, opts) => {
    board.requests.push(url + ' -> ' + opts.target);
    return Promise.resolve();
  };
  return board;
}

function changed(revision, task = null, project = PROJECT) {
  return { type: 'changed', project, task, revision };
}

function settle() {
  mock.timers.tick(1000);
}

beforeEach(() => mock.timers.enable({ apis: ['setTimeout'] }));
afterEach(() => mock.timers.reset());

test('a change the page already shows is not fetched again', () => {
  // The tab that made the change refreshed on its own and shows revision 10.
  const board = makeBoard({ content: 10 });
  board.onProjectChanged(changed(10));
  settle();
  assert.deepStrictEqual(board.requests, []);
});

test('a burst of newer changes re-renders the content once', () => {
  const board = makeBoard({ content: 10 });
  board.onProjectChanged(changed(11));
  board.onProjectChanged(changed(12));
  board.onProjectChanged(changed(13));
  settle();
  assert.deepStrictEqual(board.requests, ['/projects/p/board -> project-content']);
});

test('a change to another project is ignored', () => {
  const board = makeBoard({ content: 10 });
  board.onProjectChanged(changed(50, null, OTHER_PROJECT));
  settle();
  assert.deepStrictEqual(board.requests, []);
});

test('a change waits for the card being dragged, then lands', () => {
  const board = makeBoard({ content: 10 });
  board.dragging = 'u1';
  board.onProjectChanged(changed(11));
  settle();
  assert.deepStrictEqual(board.requests, []);

  board.onDragEnd();
  settle();
  assert.deepStrictEqual(board.requests, ['/projects/p/board -> project-content']);
});

test('a change waits for a field being typed in, then lands once focus leaves', () => {
  const board = makeBoard({ content: 10 });
  ctx.document.activeElement = focused('project-content', { field: true });
  board.onProjectChanged(changed(11));
  settle();
  assert.deepStrictEqual(board.requests, []);

  ctx.document.activeElement = null;
  board._scheduleRemoteChanges(); // what a focusout does
  settle();
  assert.equal(board.requests.length, 1);
});

test('a focused button does not hold a change back', () => {
  const board = makeBoard({ content: 10 });
  ctx.document.activeElement = focused('project-content');
  board.onProjectChanged(changed(11));
  settle();
  assert.equal(board.requests.length, 1);
});

test('an unsaved settings form holds the change back until it is saved', async () => {
  const board = makeBoard({ content: 10, view: 'settings' });
  drafts['project-content'] = true;
  board.onProjectChanged(changed(11));
  settle();
  assert.deepStrictEqual(board.requests, []);

  drafts['project-content'] = false;
  effects[0]();
  await null;
  assert.deepStrictEqual(board.requests, ['/projects/p/settings -> project-content']);
});

test('a hidden tab catches up when it is shown again', () => {
  const board = makeBoard({ content: 10 });
  ctx.document.hidden = true;
  board.onProjectChanged(changed(11));
  settle();
  assert.deepStrictEqual(board.requests, []);

  ctx.document.hidden = false;
  board._scheduleRemoteChanges(); // what visibilitychange does
  settle();
  assert.equal(board.requests.length, 1);
});

test('a change announced while a refresh loads is caught up once it lands', () => {
  const board = makeBoard({ content: 10 });
  board.onProjectChanged(changed(11));
  settle();
  assert.equal(board.requests.length, 1);

  // Revision 12 arrives while the swap is in flight.
  dom.content.attributes['aria-busy'] = 'true';
  board.onProjectChanged(changed(12));
  settle();
  assert.equal(board.requests.length, 1);

  // The fragment lands, rendered before 12 committed, and announces itself.
  dom.content = fakeElement('project-content', 11);
  board._scheduleRemoteChanges();
  settle();
  assert.equal(board.requests.length, 2);
});

test('the open task reloads for a change to it, not for one to another task', () => {
  const board = makeBoard({ content: 100, panel: 10 });
  board.panelTaskUuid = 't1';

  board.onProjectChanged(changed(11, 't2'));
  settle();
  assert.deepStrictEqual(board.requests, []);

  board.onProjectChanged(changed(12, 't1'));
  settle();
  assert.deepStrictEqual(board.requests, ['/projects/p/tasks/t1/panel -> task-panel']);
});

test('a change naming no task reloads the open one', () => {
  const board = makeBoard({ content: 100, panel: 10 });
  board.panelTaskUuid = 't1';
  board.onProjectChanged(changed(11));
  settle();
  assert.deepStrictEqual(board.requests, ['/projects/p/tasks/t1/panel -> task-panel']);
});

test('an unsent comment keeps the open task as it is until it is sent', async () => {
  const board = makeBoard({ content: 100, panel: 10 });
  board.panelTaskUuid = 't1';
  drafts['task-panel'] = true;
  board.onProjectChanged(changed(11, 't1'));
  settle();
  assert.deepStrictEqual(board.requests, []);

  drafts['task-panel'] = false;
  effects[0]();
  await null;
  assert.deepStrictEqual(board.requests, ['/projects/p/tasks/t1/panel -> task-panel']);
});

test('the open task is left to a save of it in flight', () => {
  const board = makeBoard({ content: 100, panel: 10 });
  board.panelTaskUuid = 't1';
  board._panelSaves = 1;
  board.onProjectChanged(changed(11, 't1'));
  settle();
  assert.deepStrictEqual(board.requests, []);
});

test('a refresh keeps the reader where they had scrolled', () => {
  const column = { dataset: { keepScroll: 'board-columns' }, scrollTop: 0, scrollLeft: 300 };
  const before = fakeElement('project-content', 10, {
    scrollTop: 120,
    querySelectorAll: () => [column],
  });
  const freshColumn = { scrollTop: 0, scrollLeft: 0 };
  const after = fakeElement('project-content', 11, {
    querySelector: (selector) =>
      selector === '[data-keep-scroll="board-columns"]' ? freshColumn : null,
  });
  let current = before;
  ctx.document = { getElementById: () => current };

  const restore = ctx.projectBoardHelpers.keepScroll(before);
  current = after;
  restore();

  assert.equal(after.scrollTop, 120);
  assert.equal(freshColumn.scrollLeft, 300);
});
