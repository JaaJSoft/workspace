const assert = require('node:assert');
const { test } = require('node:test');

const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/projects/ui/static/projects/ui/js/board.js');

test('moveUuid moves an item to a later index', () => {
  const result = Array.from(
    ctx.projectBoardHelpers.moveUuid(['a', 'b', 'c'], 'a', 2)
  );
  assert.deepStrictEqual(result, ['b', 'c', 'a']);
});

test('moveUuid moves an item to the front', () => {
  const result = Array.from(
    ctx.projectBoardHelpers.moveUuid(['a', 'b', 'c'], 'c', 0)
  );
  assert.deepStrictEqual(result, ['c', 'a', 'b']);
});

test('moveUuid inserts a foreign uuid (cross-column drop)', () => {
  const result = Array.from(ctx.projectBoardHelpers.moveUuid(['a', 'b'], 'x', 1));
  assert.deepStrictEqual(result, ['a', 'x', 'b']);
});

test('moveUuid clamps out-of-range indexes', () => {
  const result = Array.from(ctx.projectBoardHelpers.moveUuid(['a', 'b'], 'a', 99));
  assert.deepStrictEqual(result, ['b', 'a']);
});
