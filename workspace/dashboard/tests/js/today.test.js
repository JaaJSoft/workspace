'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/dashboard/static/dashboard/js/today.js');

// [source, urgent] for each row, in the order the server rendered them.
const ROWS = [
  ['tasks', true],
  ['mail', true],
  ['events', false],
  ['events', false],
  ['tasks', false],
];

function shown(card) {
  return ROWS.map(([source, urgent], index) => card.shows(source, urgent, index));
}

test('all shows the first rows up to the limit', () => {
  const card = ctx.todayCard(3);
  assert.deepStrictEqual(shown(card), [true, true, true, false, false]);
});

test('expanding all shows every row', () => {
  const card = ctx.todayCard(3);
  card.expanded = true;
  assert.deepStrictEqual(shown(card), [true, true, true, true, true]);
});

test('urgent shows the urgent rows of every source', () => {
  const card = ctx.todayCard(3);
  card.pick('urgent');
  assert.deepStrictEqual(shown(card), [true, true, false, false, false]);
});

test('a source filter shows that source only, past the limit too', () => {
  const card = ctx.todayCard(1);
  card.pick('events');
  assert.deepStrictEqual(shown(card), [false, false, true, true, false]);
});

test('picking a filter folds the list again', () => {
  const card = ctx.todayCard(3);
  card.expanded = true;
  card.pick('all');
  assert.strictEqual(card.expanded, false);
});
