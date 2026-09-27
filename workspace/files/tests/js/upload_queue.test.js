'use strict';

// The upload queue behind the Files upload panel, driven through a fake
// transport so each request can be answered, failed or left in flight.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('../../../common/tests/js/loader');

function makeQueue({ discardOk = true, confirmAnswer = true } = {}) {
  const requests = [];
  const notified = [];
  const confirms = [];
  const discarded = [];
  const ctx = loadScripts(
    ['workspace/common/static/ui/js/filesize.js', 'workspace/files/ui/static/files/ui/js/upload_queue.js'],
    { document: { addEventListener: () => {} } },
  );
  const queue = ctx.createUploadQueue({
    send: (file, { folderId, onConflict }, onProgress) => {
      const request = { file, folderId, onConflict, onProgress, aborted: false };
      request.promise = new Promise((resolve, reject) => {
        request.resolve = resolve;
        request.reject = reject;
      });
      request.abort = () => {
        request.aborted = true;
        request.reject(new Error('Cancelled'));
      };
      requests.push(request);
      return request;
    },
    discard: async (uuid) => { discarded.push(uuid); return discardOk; },
    notify: (type) => notified.push(type),
    confirm: async (options) => { confirms.push(options); return confirmAnswer; },
  });
  return { queue, requests, notified, confirms, discarded };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));
const file = (name, size = 100) => ({ name, size });
const statuses = (queue) => Array.from(queue.items, (item) => `${item.name}:${item.status}`);

test('files added while others upload join the same queue and count', async () => {
  const { queue, requests } = makeQueue();
  queue.add(Array.from({ length: 10 }, (_, i) => ({ file: file(`photo-${i}.jpg`), folderId: 'a' })));
  requests[0].resolve({ status: 201, body: { uuid: 'u0', name: 'photo-0.jpg' } });
  await settle();
  requests[1].onProgress(50);

  queue.add(Array.from({ length: 5 }, (_, i) => ({ file: file(`more-${i}.jpg`), folderId: 'b' })));

  assert.equal(queue.items.length, 15);
  assert.equal(queue.pendingCount, 14);
  assert.equal(queue.title, 'Uploading 14 files');
  assert.deepEqual({ ...queue.progress }, { loaded: 150, total: 1500, percent: 10 });
  assert.equal(requests.length, 2, 'the second batch waits its turn');
});

test('the queue drains in order and announces the change once', async () => {
  const { queue, requests, notified } = makeQueue();
  queue.add([{ file: file('a.txt'), folderId: 'f' }, { file: file('b.txt'), folderId: 'f' }]);
  requests[0].resolve({ status: 201, body: { uuid: 'u1', name: 'a.txt' } });
  await settle();
  assert.deepEqual(notified, [], 'nothing is announced while files remain');
  requests[1].resolve({ status: 201, body: { uuid: 'u2', name: 'b (1).txt' } });
  await settle();

  assert.deepEqual(statuses(queue), ['a.txt:done', 'b.txt:done']);
  assert.deepEqual(notified, ['uploads-changed']);
  assert.equal(queue.statusText(queue.items[1]), 'Saved as b (1).txt');
  assert.equal(queue.title, '2 uploaded');
});

test('cancelling the file in flight aborts it and moves on', async () => {
  const { queue, requests } = makeQueue();
  queue.add([{ file: file('big.mov') }, { file: file('next.jpg') }]);
  queue.cancel(queue.items[0].id);
  await settle();

  assert.equal(requests[0].aborted, true);
  assert.deepEqual(statuses(queue), ['big.mov:cancelled', 'next.jpg:uploading']);
  assert.equal(requests.length, 2);
});

test('a cancelled file uploads again on retry', async () => {
  const { queue, requests } = makeQueue();
  queue.add([{ file: file('big.mov'), folderId: 'f', onConflict: 'rename' }]);
  queue.cancel(queue.items[0].id);
  await settle();
  queue.retry(queue.items[0].id);

  assert.equal(requests.length, 2);
  assert.equal(requests[1].folderId, 'f');
  assert.equal(requests[1].onConflict, 'rename');
  assert.deepEqual(statuses(queue), ['big.mov:uploading']);
});

test('pausing lets the file in flight finish and holds the rest', async () => {
  const { queue, requests } = makeQueue();
  queue.add([{ file: file('a.jpg') }, { file: file('b.jpg') }]);
  queue.pause();

  assert.equal(requests[0].aborted, false);
  assert.equal(queue.title, 'Pausing after the current file');
  requests[0].resolve({ status: 201, body: { uuid: 'u1', name: 'a.jpg' } });
  await settle();

  assert.deepEqual(statuses(queue), ['a.jpg:done', 'b.jpg:queued']);
  assert.equal(queue.title, 'Paused - 1 file left');
  assert.equal(queue.statusText(queue.items[1]), 'Paused');
  assert.equal(requests.length, 1, 'nothing starts while paused');

  queue.resume();
  assert.equal(requests.length, 2);
  assert.equal(requests[1].file.name, 'b.jpg');
});

test('a late answer to a cancelled request is ignored', async () => {
  const { queue, requests } = makeQueue();
  queue.add([{ file: file('a.jpg') }]);
  queue.cancel(queue.items[0].id);
  queue.retry(queue.items[0].id);
  requests[0].resolve({ status: 201, body: { uuid: 'stale', name: 'a.jpg' } });
  await settle();

  assert.deepEqual(statuses(queue), ['a.jpg:uploading']);
  assert.equal(queue.items[0].uuid, null);
});

test('a failure is kept with its reason and does not stop the queue', async () => {
  const { queue, requests } = makeQueue();
  queue.add([{ file: file('a.jpg') }, { file: file('b.jpg') }]);
  requests[0].reject(new Error('Storage quota exceeded'));
  await settle();

  assert.deepEqual(statuses(queue), ['a.jpg:failed', 'b.jpg:uploading']);
  assert.equal(queue.statusText(queue.items[0]), 'Storage quota exceeded');
});

test('a name the server refuses marks the file skipped', async () => {
  const { queue, requests } = makeQueue();
  queue.add([{ file: file('a.jpg') }]);
  const refused = new Error('exists');
  refused.nameCollision = true;
  requests[0].reject(refused);
  await settle();

  assert.deepEqual(statuses(queue), ['a.jpg:skipped']);
  assert.equal(queue.statusText(queue.items[0]), 'A file with this name already exists');
});

test('a file the collision check skipped is listed but never sent', () => {
  const { queue, requests } = makeQueue();
  queue.add([{ file: file('a.jpg'), onConflict: 'skip' }, { file: file('b.jpg') }]);

  assert.deepEqual(statuses(queue), ['a.jpg:skipped', 'b.jpg:uploading']);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].file.name, 'b.jpg');
});

test('a duplicate upload is flagged and can be discarded from the panel', async () => {
  const { queue, requests, discarded, notified } = makeQueue();
  queue.add([{ file: file('a.jpg') }]);
  requests[0].resolve({
    status: 201,
    body: { uuid: 'u1', name: 'a.jpg', duplicates: [{ path: 'Photos/a.jpg' }, { path: 'Old/a.jpg' }] },
  });
  await settle();
  assert.equal(queue.statusText(queue.items[0]), 'Same content as Photos/a.jpg and 1 more');

  await queue.discard(queue.items[0].id);

  assert.deepEqual(discarded, ['u1']);
  assert.deepEqual(statuses(queue), ['a.jpg:discarded']);
  assert.deepEqual(notified, ['uploads-changed', 'uploads-changed']);
});

test('a replaced file is never offered for discard', async () => {
  const { queue, requests } = makeQueue();
  queue.add([{ file: file('a.jpg'), onConflict: 'replace' }]);
  requests[0].resolve({ status: 200, body: { uuid: 'u1', name: 'a.jpg', duplicates: [{ path: 'x' }] } });
  await settle();

  assert.equal(queue.items[0].duplicates.length, 0);
  assert.equal(queue.statusText(queue.items[0]), 'Replaced the existing file');
});

test('closing with uploads pending asks first, then cancels them', async () => {
  const { queue, requests, confirms } = makeQueue();
  queue.add([{ file: file('a.jpg') }, { file: file('b.jpg') }]);
  await queue.close();

  assert.equal(confirms.length, 1);
  assert.equal(confirms[0].message, '2 files have not finished uploading.');
  assert.equal(requests[0].aborted, true);
  assert.equal(queue.items.length, 0);
});

test('declining the close prompt keeps uploading', async () => {
  const { queue, requests } = makeQueue({ confirmAnswer: false });
  queue.add([{ file: file('a.jpg') }]);
  await queue.close();

  assert.equal(requests[0].aborted, false);
  assert.deepEqual(statuses(queue), ['a.jpg:uploading']);
});

test('a queue cancelled while paused runs the next files added', async () => {
  const { queue, requests } = makeQueue();
  queue.add([{ file: file('a.jpg') }]);
  queue.pause();
  queue.cancelAll();
  queue.add([{ file: file('b.jpg') }]);

  assert.equal(queue.paused, false);
  assert.equal(requests.at(-1).file.name, 'b.jpg');
});

test('rows that need the user are listed first', async () => {
  const { queue, requests } = makeQueue();
  queue.add([{ file: file('a.jpg') }, { file: file('b.jpg') }, { file: file('c.jpg') }]);
  requests[0].resolve({ status: 201, body: { uuid: 'u1', name: 'a.jpg' } });
  await settle();
  requests[1].reject(new Error('Network error'));
  await settle();

  assert.deepEqual(
    Array.from(queue.rows, (item) => `${item.name}:${item.status}`),
    ['b.jpg:failed', 'a.jpg:done', 'c.jpg:uploading'],
  );
});
