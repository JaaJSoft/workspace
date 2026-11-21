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

test('the save queue starts a task only once the previous one settled', async () => {
  const enqueue = ctx.todaySaveQueue();
  const log = [];
  let finishFirst;
  enqueue(() => new Promise((resolve) => {
    log.push('first started');
    finishFirst = () => { log.push('first done'); resolve(); };
  }));
  const second = enqueue(() => { log.push('second started'); });

  await new Promise((resolve) => setImmediate(resolve));
  assert.deepStrictEqual(log, ['first started']);
  finishFirst();
  await second;
  assert.deepStrictEqual(log, ['first started', 'first done', 'second started']);
});

test('a failed save does not block the next one', async () => {
  const enqueue = ctx.todaySaveQueue();
  enqueue(() => Promise.reject(new Error('offline'))).catch(() => {});
  let ran = false;
  await enqueue(() => { ran = true; });
  assert.strictEqual(ran, true);
});
