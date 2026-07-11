'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

/**
 * Fake <li id="conv-item-..."> sidebar row.
 *
 * @param {object} opts
 * @param {boolean} opts.pinned - pinned rows carry the draggable attribute
 */
function fakeRow({ pinned = false } = {}) {
  return {
    hasAttribute(name) { return name === 'draggable' && pinned; },
    parentElement: null,
  };
}

/**
 * Fake <ul> holding rows in order, with the subset of the DOM API that
 * _moveConversationItemToTop touches.
 */
function fakeList(rows) {
  const ul = {
    tagName: 'UL',
    rows,
    get firstElementChild() { return rows[0] || null; },
    prepend(el) {
      const i = rows.indexOf(el);
      if (i !== -1) rows.splice(i, 1);
      rows.unshift(el);
    },
  };
  for (const row of rows) row.parentElement = ul;
  return ul;
}

/**
 * Build a chatConversationsMixin() with $ajax and the full-list refresh
 * stubbed, backed by a fake DOM keyed by element id.
 *
 * @param {object} dom - id -> fake element map
 * @param {object} [opts]
 * @param {boolean} [opts.ajaxFails] - whether the stubbed $ajax rejects
 */
function buildApp(dom, { ajaxFails = false } = {}) {
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/conversations.js', {
    document: { getElementById: (id) => dom[id] || null },
  });

  const calls = { ajax: [], fullRefresh: 0 };
  const app = ctx.chatConversationsMixin();
  Object.assign(app, {
    refreshConversationList() { calls.fullRefresh++; },
    async $ajax(url, options) {
      calls.ajax.push({ url, options });
      if (ajaxFails) throw new Error('network down');
    },
  });
  return { app, calls };
}

test('refreshes only the targeted rows and moves them to the top of their list', async () => {
  const rowA = fakeRow();
  const rowB = fakeRow();
  const ul = fakeList([rowB, rowA]); // rowA starts second
  const { app, calls } = buildApp({ 'conv-item-a': rowA, 'conv-item-b': rowB });

  await app.refreshConversationItems(['a']);

  assert.equal(calls.fullRefresh, 0, 'full-list refresh should not run when the row exists');
  assert.equal(calls.ajax.length, 1);
  assert.equal(calls.ajax[0].url, '/chat/conversations/items?uuids=a');
  assert.deepStrictEqual(Array.from(calls.ajax[0].options.targets), ['conv-item-a']);
  assert.equal(ul.rows[0], rowA, 'the refreshed row should be moved to the top');
});

test('deduplicates uuids before building the request', async () => {
  const rowA = fakeRow();
  fakeList([rowA]);
  const { app, calls } = buildApp({ 'conv-item-a': rowA });

  await app.refreshConversationItems(['a', 'a', null]);

  assert.equal(calls.ajax.length, 1);
  assert.equal(calls.ajax[0].url, '/chat/conversations/items?uuids=a');
  assert.deepStrictEqual(Array.from(calls.ajax[0].options.targets), ['conv-item-a']);
});

test('falls back to the full-list refresh when a row is missing from the DOM', async () => {
  const rowA = fakeRow();
  fakeList([rowA]);
  const { app, calls } = buildApp({ 'conv-item-a': rowA });

  await app.refreshConversationItems(['a', 'new-conv']);

  assert.equal(calls.ajax.length, 0, 'no targeted swap when a row is missing');
  assert.equal(calls.fullRefresh, 1, 'the full-list refresh should handle new conversations');
});

test('falls back to the full-list refresh when the targeted swap fails', async () => {
  const rowA = fakeRow();
  const ul = fakeList([fakeRow(), rowA]);
  const { app, calls } = buildApp({ 'conv-item-a': rowA }, { ajaxFails: true });

  await app.refreshConversationItems(['a']);

  assert.equal(calls.fullRefresh, 1, 'a failed swap should recover with a full refresh');
  assert.notEqual(ul.rows[0], rowA, 'the row should not be reordered after a failed swap');
});

test('pinned rows are refreshed in place without reordering', async () => {
  const pinnedRow = fakeRow({ pinned: true });
  const ul = fakeList([fakeRow({ pinned: true }), pinnedRow]);
  const { app, calls } = buildApp({ 'conv-item-p': pinnedRow });

  await app.refreshConversationItems(['p']);

  assert.equal(calls.ajax.length, 1, 'pinned rows still get the targeted swap');
  assert.equal(ul.rows[1], pinnedRow, 'pinned rows keep their manual pin order');
});

test('does nothing for an empty uuid list', async () => {
  const { app, calls } = buildApp({});

  await app.refreshConversationItems([]);

  assert.equal(calls.ajax.length, 0);
  assert.equal(calls.fullRefresh, 0);
});
