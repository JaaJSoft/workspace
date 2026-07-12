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
