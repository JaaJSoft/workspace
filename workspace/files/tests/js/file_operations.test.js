'use strict';

// The client of the batch operations API: what it sends, how it follows an
// operation to its end with or without the event stream, and what the
// transfers panel gets to show.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('../../../common/tests/js/loader');

const URL = '/api/v1/files/operations';

function operation(fields = {}) {
  return {
    uuid: 'op-1',
    kind: 'move',
    status: 'pending',
    target: 'dst',
    target_name: 'Reports',
    total: 3,
    processed: 0,
    bytes_total: null,
    bytes_done: 0,
    counts: {},
    errors: [],
    cancel_requested: false,
    ...fields,
  };
}

function makeClient(answer) {
  const requests = [];
  const notified = [];
  const timers = [];
  const ctx = loadScripts([
    'workspace/files/ui/static/files/ui/js/file_actions.js',
    'workspace/files/ui/static/files/ui/js/file_operations.js',
  ], { document: { addEventListener: () => {} } });
  const store = ctx.createFileOperations({
    request: async (url, { method, body }) => {
      requests.push([method, url, body === undefined ? undefined : JSON.parse(JSON.stringify(body))]);
      const [status, data] = answer(method, url, body);
      return { ok: status < 400, status, body: data };
    },
    notify: (type, detail) => notified.push([type, detail.uuid]),
    schedule: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    unschedule: (id) => { timers[id - 1].cancelled = true; },
  });
  // Runs the timers due at *ms*, the display delay and the polls alike.
  const fire = async (ms) => {
    for (const timer of timers.filter((t) => t.ms === ms && !t.cancelled && !t.fired)) {
      timer.fired = true;
      await timer.fn();
    }
    await settle();
  };
  return { ctx, store, requests, notified, timers, fire };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));
const plain = (value) => JSON.parse(JSON.stringify(value));

test('a run posts the items and the destination as the API takes them', async () => {
  const { store, requests } = makeClient(() => [202, operation({ status: 'completed', counts: { done: 2 } })]);

  await store.run('move', ['a', { uuid: 'b', on_conflict: 'replace' }], { target: 'dst' });

  assert.deepEqual(plain(requests[0]), [
    'POST', URL,
    { kind: 'move', items: [{ uuid: 'a' }, { uuid: 'b', on_conflict: 'replace' }], target: 'dst' },
  ]);
});

test('emptying the trash names no items', async () => {
  const { store, requests } = makeClient(() => [202, operation({ kind: 'empty_trash', status: 'completed' })]);

  await store.run('empty_trash');

  assert.deepEqual(plain(requests[0]), ['POST', URL, { kind: 'empty_trash' }]);
});

test('a quick clean operation resolves at once and leaves nothing behind', async () => {
  const { store, requests, notified } = makeClient((method) => (
    method === 'POST' ? [202, operation({ status: 'completed', processed: 3, counts: { done: 3 } })] : [204, null]
  ));

  const report = await store.run('move', ['a', 'b', 'c'], { target: 'dst' });

  assert.deepEqual(plain(report.counts), { done: 3 });
  assert.equal(store.items.length, 0, 'never shown, so not kept');
  assert.deepEqual(plain(requests.at(-1).slice(0, 2)), ['DELETE', `${URL}/op-1`]);
  assert.deepEqual(plain(notified), [['file-operation-finished', 'op-1']]);
});

test('a refused request rejects with the server reason', async () => {
  const { store } = makeClient(() => [400, { target: ['Folder not found.'] }]);

  await assert.rejects(store.run('move', ['a'], { target: 'gone' }), (error) => {
    assert.equal(error.message, 'Folder not found.');
    assert.equal(error.status, 400);
    return true;
  });
});

test('an operation that lasts shows in the panel and follows the event stream', async () => {
  const failed = operation({
    status: 'completed', processed: 3, counts: { done: 2, failed: 1 },
    errors: [{ uuid: 'c', name: 'c.txt', reason: 'name_taken', message: 'Taken.' }],
  });
  const { store, requests, fire } = makeClient((method) => (
    method === 'POST' ? [202, operation()] : [200, failed]
  ));

  const finished = store.run('move', ['a', 'b', 'c'], { target: 'dst' });
  await settle();
  assert.equal(store.rows.length, 0, 'hidden for its first half second');
  await fire(500);
  assert.equal(store.rows.length, 1);
  assert.equal(store.activeCount, 1);

  store.receive({ operation_uuid: 'op-1', status: 'running', total: 3, processed: 1, counts: { done: 1 } });
  assert.equal(store.rows[0].processed, 1);
  store.receive({ operation_uuid: 'op-1', status: 'completed', total: 3, processed: 3, counts: { done: 2, failed: 1 } });
  const report = await finished;

  assert.deepEqual(plain(report.errors), plain(failed.errors), 'the errors come from the report');
  assert.deepEqual(plain(requests.at(-1).slice(0, 2)), ['GET', `${URL}/op-1`]);
  assert.equal(store.rows.length, 1, 'kept until dismissed');
  assert.equal(store.attentionCount, 1);
});

test('without the event stream the operation is polled to its end', async () => {
  let polls = 0;
  const { store, fire } = makeClient((method) => {
    if (method === 'POST') return [202, operation()];
    if (method === 'DELETE') return [204, null];
    polls += 1;
    return [200, operation(polls < 2 ? { status: 'running', processed: 1 } : { status: 'completed', processed: 3, counts: { done: 3 } })];
  });

  const finished = store.run('trash', ['a', 'b', 'c']);
  await settle();
  await fire(1000);
  assert.equal(store.items[0].processed, 1);
  await fire(2000);
  const report = await finished;

  assert.equal(report.status, 'completed');
  assert.equal(polls, 2, 'a finished report from a poll needs no second read');
});

test('a quick operation that failed items is shown anyway', async () => {
  const { store } = makeClient(() => [202, operation({
    status: 'completed', processed: 1, total: 1, counts: { failed: 1 },
    errors: [{ uuid: 'a', name: 'a.txt', reason: 'locked', message: 'Locked.' }],
  })]);

  await store.run('trash', ['a']);

  assert.equal(store.rows.length, 1);
});

test('reloading the page brings back what still runs or needs a look', async () => {
  const { store } = makeClient(() => [200, [
    operation({ uuid: 'running', status: 'running' }),
    operation({ uuid: 'failed', status: 'completed', counts: { failed: 1 } }),
    operation({ uuid: 'clean', status: 'completed', counts: { done: 3 } }),
  ]]);

  await store.restore();

  assert.deepEqual(Array.from(store.rows, (row) => row.uuid), ['running', 'failed'], 'newest first, as listed');
  assert.equal(store.panel, true);
});

test('cancel and dismiss go to the server', async () => {
  const { store, requests } = makeClient((method, url) => {
    if (url.endsWith('/cancel')) return [200, operation({ status: 'cancelled', cancel_requested: true })];
    return method === 'POST' ? [202, operation()] : [204, null];
  });
  store.run('copy', ['a'], { target: 'dst' });
  await settle();

  await store.cancel('op-1');
  await settle();
  store.items[0].shown = true;
  store.dismiss('op-1');

  assert.deepEqual(
    plain(requests.map(([method, url]) => [method, url])),
    [['POST', URL], ['POST', `${URL}/op-1/cancel`], ['DELETE', `${URL}/op-1`]],
  );
  assert.equal(store.items.length, 0);
});

test('the panel describes each kind and how it ended', () => {
  const { ctx } = makeClient(() => [200, null]);

  assert.equal(ctx.describeOperation(operation()), 'Move 3 items to Reports');
  assert.equal(ctx.describeOperation(operation({ kind: 'copy', target_name: '' })), 'Copy 3 items to My files');
  assert.equal(ctx.describeOperation(operation({ kind: 'trash', total: 1 })), 'Move 1 item to the trash');
  assert.equal(ctx.describeOperation(operation({ kind: 'empty_trash' })), 'Empty the trash');
  assert.equal(ctx.operationStatusText(operation({ status: 'running', processed: 1 })), '1 of 3');
  assert.equal(
    ctx.operationStatusText(operation({ status: 'completed', counts: { done: 2, failed: 1 } })),
    '2 done, 1 failed',
  );
  assert.equal(ctx.operationStatusText(operation({ status: 'cancelled', processed: 1 })), 'Cancelled after 1 of 3');
  assert.equal(ctx.operationPercent(operation({ status: 'running', processed: 1 })), 33);
  assert.equal(
    ctx.operationPercent(operation({ status: 'running', bytes_total: 200, bytes_done: 50 })),
    25,
    'a copy counts bytes',
  );
});
