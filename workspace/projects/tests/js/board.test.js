const assert = require('node:assert');
const { test } = require('node:test');

const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/projects/ui/static/projects/ui/js/board.js');

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

function deletableBoard(calls) {
  ctx.getCSRFToken = () => 'token';
  ctx.localStorage = { getItem: () => null, setItem: () => {} };
  const board = ctx.projectBoard({ apiBase: '/api', writable: true });
  board.form.uuid = 'u1';
  board.form.title = 'Task one';
  board.taskActions = ['delete'];
  board.$refs = {
    taskDialog: {
      close() {
        calls.push('close');
      },
    },
  };
  board.refresh = () => calls.push('refresh');
  return board;
}

test('deleteTask aborts without a request when confirmation is declined', async () => {
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
  const board = deletableBoard(calls);
  await board.deleteTask();
  assert.deepStrictEqual(Array.from(calls), ['confirm']);
});

test('deleteTask deletes and refreshes once confirmed', async () => {
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
  const board = deletableBoard(calls);
  await board.deleteTask();
  assert.deepStrictEqual(Array.from(calls), [
    'confirm',
    'DELETE /api/tasks/u1',
    'close',
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
