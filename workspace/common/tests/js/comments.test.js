'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('./loader');

const LIST_URL = '/api/v1/projects/p-uuid/tasks/t-uuid/comments';

function make({ canComment = true, fetchImpl } = {}) {
  const calls = [];
  const defaultFetch = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    return { ok: true, json: async () => [] };
  };
  const ctx = loadScript('workspace/common/static/ui/js/comments.js', {
    fetch: fetchImpl || defaultFetch,
    getCSRFToken: () => 'tok',
  });
  const comp = ctx.commentsComponent(LIST_URL, 7, canComment);
  comp.$refs = {};
  return { comp, calls };
}

test('builds list and detail URLs from the base', () => {
  const { comp } = make();
  assert.equal(comp._url(), LIST_URL);
  assert.equal(comp._url('c1'), `${LIST_URL}/c1`);
});

test('addComment posts the trimmed body then reloads the list', async () => {
  const { comp, calls } = make();
  comp.newBody = '  hello  ';
  await comp.addComment();
  assert.deepEqual(
    calls.map((c) => c.method),
    ['POST', 'GET'],
  );
  assert.equal(comp.newBody, '');
  assert.equal(comp.sending, false);
});

test('addComment is inert without canComment', async () => {
  const { comp, calls } = make({ canComment: false });
  comp.newBody = 'hello';
  await comp.addComment();
  assert.equal(calls.length, 0);
  assert.equal(comp.newBody, 'hello');
});

test('addComment is inert on empty body', async () => {
  const { comp, calls } = make();
  comp.newBody = '   ';
  await comp.addComment();
  assert.equal(calls.length, 0);
});

test('deleteComment and saveEdit are inert without canComment', async () => {
  const { comp, calls } = make({ canComment: false });
  comp.editBody = 'x';
  await comp.saveEdit('c1');
  await comp.deleteComment('c1');
  assert.equal(calls.length, 0);
});

test('startEdit/cancelEdit toggle the editing state', () => {
  const { comp } = make();
  comp.startEdit({ uuid: 'c1', body: 'original' });
  assert.equal(comp.editingId, 'c1');
  assert.equal(comp.editBody, 'original');
  comp.cancelEdit();
  assert.equal(comp.editingId, null);
  assert.equal(comp.editBody, '');
});

test('saveEdit patches then clears the editing state', async () => {
  const { comp, calls } = make();
  comp.startEdit({ uuid: 'c1', body: 'original' });
  comp.editBody = 'fixed';
  await comp.saveEdit('c1');
  assert.deepEqual(
    calls.map((c) => [c.method, c.url]),
    [
      ['PATCH', `${LIST_URL}/c1`],
      ['GET', LIST_URL],
    ],
  );
  assert.equal(comp.editingId, null);
});

test('failed load leaves comments empty and stops the spinner', async () => {
  const { comp } = make({
    fetchImpl: async () => {
      throw new Error('network down');
    },
  });
  await comp.loadComments();
  assert.deepEqual(Array.from(comp.comments), []);
  assert.equal(comp.loading, false);
});

test('autoGrow sizes the element to its content plus borders', () => {
  const { comp } = make();
  const el = { style: {}, scrollHeight: 100, offsetHeight: 52, clientHeight: 50 };
  comp.autoGrow(el);
  assert.equal(el.style.height, '102px');
});

test('formatDate buckets relative times', () => {
  const { comp } = make();
  const now = Date.now();
  assert.equal(comp.formatDate(new Date(now - 30 * 1000).toISOString()), 'just now');
  assert.equal(comp.formatDate(new Date(now - 5 * 60 * 1000).toISOString()), '5m ago');
  assert.equal(comp.formatDate(new Date(now - 3 * 3600 * 1000).toISOString()), '3h ago');
  assert.equal(comp.formatDate(new Date(now - 2 * 86400 * 1000).toISOString()), '2d ago');
});
