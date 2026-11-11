'use strict';

// The upload queue behind the Files upload panel, driven through a fake
// transport so each request can be answered, failed or left in flight.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('../../../common/tests/js/loader');

function makeQueue({ discardOk = () => true, confirmAnswer = true } = {}) {
  const requests = [];
  const notified = [];
  const confirms = [];
  const discarded = [];
  const discardCalls = [];
  const released = [];
  const ctx = loadScripts(
    ['workspace/common/static/ui/js/filesize.js', 'workspace/files/ui/static/files/ui/js/upload_queue.js'],
    { document: { addEventListener: () => {} } },
  );
  const queue = ctx.createUploadQueue({
    send: (file, { folderId, onConflict }, onProgress, onRetrying) => {
      const request = { file, folderId, onConflict, onProgress, onRetrying, aborted: false };
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
    discard: async (uploads) => {
      discardCalls.push(Array.from(uploads, (upload) => upload.name));
      const uuids = Array.from(uploads, (upload) => upload.uuid).filter(discardOk);
      discarded.push(...uuids);
      return uuids;
    },
    notify: (type) => notified.push(type),
    confirm: async (options) => { confirms.push(options); return confirmAnswer; },
    release: (file) => released.push(file.name),
  });
  return { queue, requests, notified, confirms, discarded, discardCalls, released };
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
  assert.equal(requests.length, 4, 'the second batch waits its turn');
});

test('three files go up at once, and a slot that frees up is refilled', async () => {
  const { queue, requests, notified } = makeQueue();
  queue.add(Array.from({ length: 5 }, (_, i) => ({ file: file(`f-${i}.txt`) })));

  assert.deepEqual(Array.from(requests, (r) => r.file.name), ['f-0.txt', 'f-1.txt', 'f-2.txt']);
  assert.deepEqual(statuses(queue).slice(3), ['f-3.txt:queued', 'f-4.txt:queued']);

  requests[1].resolve({ status: 201, body: { uuid: 'u1', name: 'f-1.txt' } });
  await settle();
  assert.equal(requests.length, 4);
  assert.equal(requests[3].file.name, 'f-3.txt');

  for (const i of [0, 2, 3]) requests[i].resolve({ status: 201, body: { uuid: `u${i}` } });
  await settle();
  assert.equal(requests.length, 5);
  assert.deepEqual(notified, [], 'nothing is announced while a file is in flight');

  requests[4].resolve({ status: 201, body: { uuid: 'u4' } });
  await settle();
  assert.deepEqual(notified, ['uploads-changed']);
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

test('a file whose bytes are all sent reads as finishing until the server answers', async () => {
  const { queue, requests } = makeQueue();
  queue.add([{ file: file('big.mov', 2048) }]);
  requests[0].onProgress(1024);
  assert.equal(queue.statusText(queue.items[0]), '1.0 KB of 2.0 KB');

  requests[0].onProgress(2048);
  assert.equal(queue.statusText(queue.items[0]), 'Finishing up');

  requests[0].resolve({ status: 201, body: { uuid: 'u1', name: 'big.mov' } });
  await settle();
  assert.equal(queue.statusText(queue.items[0]), '2.0 KB');
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

test('pausing lets the files in flight finish and holds the rest', async () => {
  const { queue, requests } = makeQueue();
  queue.add(['a.jpg', 'b.jpg', 'c.jpg', 'd.jpg'].map((name) => ({ file: file(name) })));
  queue.pause();

  assert.ok(requests.every((request) => !request.aborted));
  assert.equal(queue.title, 'Pausing after the 3 files in flight');
  requests[0].resolve({ status: 201, body: { uuid: 'u1', name: 'a.jpg' } });
  requests[1].resolve({ status: 201, body: { uuid: 'u2', name: 'b.jpg' } });
  await settle();
  assert.equal(queue.title, 'Pausing after the current file');
  requests[2].resolve({ status: 201, body: { uuid: 'u3', name: 'c.jpg' } });
  await settle();

  assert.deepEqual(statuses(queue), ['a.jpg:done', 'b.jpg:done', 'c.jpg:done', 'd.jpg:queued']);
  assert.equal(queue.title, 'Paused - 1 file left');
  assert.equal(queue.statusText(queue.items[3]), 'Paused');
  assert.equal(requests.length, 3, 'nothing starts while paused');

  queue.resume();
  assert.equal(requests.length, 4);
  assert.equal(requests[3].file.name, 'd.jpg');
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

async function queueWithDuplicates(names, options) {
  const harness = makeQueue(options);
  harness.queue.add(names.map((name) => ({ file: file(name) })));
  for (const [i, name] of names.entries()) {
    harness.requests[i].resolve({
      status: 201,
      body: { uuid: `u-${name}`, name, duplicates: name.startsWith('dup') ? [{ path: `Old/${name}` }] : [] },
    });
    await settle();
  }
  return harness;
}

test('every flagged duplicate can be discarded at once', async () => {
  const { queue, discarded, discardCalls, notified } = await queueWithDuplicates(['dup-a.jpg', 'new.jpg', 'dup-b.jpg']);
  assert.equal(queue.duplicateCount, 2);

  await queue.discardAllDuplicates();

  assert.deepEqual(discardCalls, [['dup-a.jpg', 'dup-b.jpg']], 'one discard for the whole batch');
  assert.deepEqual(discarded, ['u-dup-a.jpg', 'u-dup-b.jpg']);
  assert.deepEqual(statuses(queue), ['dup-a.jpg:discarded', 'new.jpg:done', 'dup-b.jpg:discarded']);
  assert.equal(queue.duplicateCount, 0);
  assert.deepEqual(notified, ['uploads-changed', 'uploads-changed'], 'one refresh for the whole batch');
});

test('two copies uploaded side by side are never both discarded', async () => {
  const { queue, requests, discarded } = makeQueue();
  queue.add([{ file: file('a.jpg') }, { file: file('b.jpg') }]);
  requests[0].resolve({ status: 201, body: { uuid: 'u-a', name: 'a.jpg', duplicates: [{ uuid: 'u-b', path: 'b.jpg' }] } });
  requests[1].resolve({ status: 201, body: { uuid: 'u-b', name: 'b.jpg', duplicates: [{ uuid: 'u-a', path: 'a.jpg' }] } });
  await settle();
  assert.equal(queue.duplicateCount, 2);

  await queue.discardAllDuplicates();

  assert.deepEqual(discarded, ['u-a']);
  assert.deepEqual(statuses(queue), ['a.jpg:discarded', 'b.jpg:done']);
  assert.equal(queue.duplicateCount, 0, 'the copy left is no longer flagged');
});

test('a duplicate the server failed to discard stays flagged', async () => {
  const { queue, notified } = await queueWithDuplicates(
    ['dup-a.jpg', 'dup-b.jpg'],
    { discardOk: (uuid) => uuid !== 'u-dup-b.jpg' },
  );

  await queue.discardAllDuplicates();

  assert.deepEqual(statuses(queue), ['dup-a.jpg:discarded', 'dup-b.jpg:done']);
  assert.equal(queue.duplicateCount, 1);
  assert.deepEqual(notified, ['uploads-changed', 'uploads-changed']);
});

test('nothing discarded refreshes nothing', async () => {
  const { queue, notified } = await queueWithDuplicates(['dup-a.jpg'], { discardOk: () => false });

  await queue.discardAllDuplicates();

  assert.deepEqual(statuses(queue), ['dup-a.jpg:done']);
  assert.deepEqual(notified, ['uploads-changed']);
});

test('every flagged duplicate can be kept at once', async () => {
  const { queue, discarded } = await queueWithDuplicates(['dup-a.jpg', 'dup-b.jpg']);

  queue.keepAllDuplicates();

  assert.equal(queue.duplicateCount, 0);
  assert.deepEqual(discarded, []);
  assert.deepEqual(statuses(queue), ['dup-a.jpg:done', 'dup-b.jpg:done']);
});

test('every failed upload can be retried at once, leaving cancelled ones alone', async () => {
  const { queue, requests } = makeQueue();
  queue.add([{ file: file('a.jpg'), folderId: 'f' }, { file: file('b.jpg') }, { file: file('c.jpg') }]);
  requests[0].reject(new Error('Network error'));
  await settle();
  queue.cancel(queue.items[1].id);
  await settle();
  requests[2].reject(new Error('Network error'));
  await settle();
  assert.equal(queue.failedCount, 2);

  queue.retryFailed();

  assert.equal(queue.failedCount, 0);
  assert.deepEqual(statuses(queue), ['a.jpg:uploading', 'b.jpg:cancelled', 'c.jpg:uploading']);
  assert.equal(requests[3].folderId, 'f', 'a retried file keeps its folder');
});

test('a row says when its upload lost the connection and is retrying', async () => {
  const { queue, requests } = makeQueue();
  queue.add([{ file: file('video.mp4', 200 * 1024 * 1024) }]);
  const [item] = queue.items;

  requests[0].onProgress(64 * 1024 * 1024);
  requests[0].onRetrying(true);
  assert.match(queue.statusText(item), /^Connection lost, retrying - 64\.0\sMB of 200\.0\sMB$/);

  requests[0].onRetrying(false);
  assert.match(queue.statusText(item), /^64\.0\sMB of 200\.0\sMB$/);

  requests[0].onRetrying(true);
  requests[0].reject(new Error('Network error'));
  await settle();
  assert.equal(item.retrying, false);
  assert.equal(queue.statusText(item), 'Network error');
});

test('closing the panel releases what failed uploads kept for a retry', async () => {
  const { queue, requests, released } = makeQueue();
  queue.add([{ file: file('a.mp4') }, { file: file('b.mp4') }]);
  requests[0].reject(new Error('Network error'));
  requests[1].resolve({ status: 201, body: { uuid: 'u1', name: 'b.mp4' } });
  await settle();

  await queue.close();

  assert.deepEqual(released, ['a.mp4']);
});

const networkError = () => Object.assign(new Error('Network error'), { transient: true });

test('waking the queue starts again what failed on the network, not what was refused', async () => {
  const { queue, requests } = makeQueue();
  queue.add([{ file: file('cut.jpg'), folderId: 'f' }, { file: file('refused.jpg') }]);
  requests[0].reject(networkError());
  requests[1].reject(new Error('Storage quota exceeded'));
  await settle();
  assert.deepEqual(statuses(queue), ['cut.jpg:failed', 'refused.jpg:failed']);

  queue.wake();

  assert.deepEqual(statuses(queue), ['cut.jpg:uploading', 'refused.jpg:failed']);
  assert.equal(requests.length, 3);
  assert.equal(requests[2].folderId, 'f');
  assert.equal(queue.items[0].error, '');
});

test('a file started again by a wake that fails on a refusal stays failed', async () => {
  const { queue, requests } = makeQueue();
  queue.add([{ file: file('a.jpg') }]);
  requests[0].reject(networkError());
  await settle();
  queue.wake();
  requests[1].reject(new Error('Storage quota exceeded'));
  await settle();

  queue.wake();

  assert.deepEqual(statuses(queue), ['a.jpg:failed']);
  assert.equal(requests.length, 2);
});

test('waking the queue wakes the uploads in flight', () => {
  const { queue, requests } = makeQueue();
  queue.add([{ file: file('a.mp4') }, { file: file('b.mp4') }]);
  let woken = 0;
  requests[0].wake = () => { woken += 1; };

  queue.wake();

  assert.equal(woken, 1, 'a request without wake() is left alone');
  assert.equal(requests.length, 2);
});

test('a paused queue woken up holds what it paused', async () => {
  const { queue, requests } = makeQueue();
  queue.add([{ file: file('a.jpg') }]);
  queue.pause();
  requests[0].reject(networkError());
  await settle();

  queue.wake();

  assert.deepEqual(statuses(queue), ['a.jpg:queued']);
  assert.equal(requests.length, 1);
});

test('the queue is transferring while a file goes up or waits for a slot', async () => {
  const { queue, requests } = makeQueue();
  assert.equal(queue.transferring, false);

  queue.add([{ file: file('a.jpg') }, { file: file('b.jpg') }, { file: file('c.jpg') }, { file: file('d.jpg') }]);
  assert.equal(queue.transferring, true);

  queue.pause();
  for (const request of requests) request.resolve({ status: 201, body: {} });
  await settle();
  assert.equal(queue.transferring, false, 'a paused queue sends nothing');

  queue.resume();
  assert.equal(queue.transferring, true);
  requests[3].resolve({ status: 201, body: {} });
  await settle();
  assert.equal(queue.transferring, false);
});

function makeWakeLock({ visible = true, refuse = false } = {}) {
  const page = { visible };
  const locks = [];
  const api = {
    request: (type) => {
      if (refuse) return Promise.reject(new Error('NotAllowedError'));
      const listeners = [];
      const lock = {
        type,
        released: false,
        addEventListener: (name, fn) => { if (name === 'release') listeners.push(fn); },
        release: async () => { lock.released = true; },
        // What the browser does when the page is hidden.
        drop: () => { lock.released = true; listeners.forEach((fn) => fn()); },
      };
      locks.push(lock);
      return Promise.resolve(lock);
    },
  };
  const ctx = loadScripts(
    ['workspace/common/static/ui/js/filesize.js', 'workspace/files/ui/static/files/ui/js/upload_queue.js'],
    { document: { addEventListener: () => {} } },
  );
  return { ctx, api, page, locks, isVisible: () => page.visible };
}

test('the screen is kept on while held, and let go after', async () => {
  const { ctx, api, locks, isVisible } = makeWakeLock();
  const screen = ctx.createScreenWakeLock(api, isVisible);

  screen.hold(true);
  screen.hold(true);
  await settle();
  assert.equal(locks.length, 1);
  assert.equal(locks[0].type, 'screen');

  screen.hold(false);
  assert.equal(locks[0].released, true);
});

test('a screen lock the browser dropped is taken again once the page is shown', async () => {
  const { ctx, api, page, locks, isVisible } = makeWakeLock();
  const screen = ctx.createScreenWakeLock(api, isVisible);
  screen.hold(true);
  await settle();

  page.visible = false;
  locks[0].drop();
  screen.refresh();
  await settle();
  assert.equal(locks.length, 1, 'a hidden page cannot take one');

  page.visible = true;
  screen.refresh();
  await settle();
  assert.equal(locks.length, 2);
  assert.equal(locks[1].released, false);
});

test('a screen lock granted after it stopped being wanted is let go', async () => {
  const { ctx, api, locks, isVisible } = makeWakeLock();
  const screen = ctx.createScreenWakeLock(api, isVisible);

  screen.hold(true);
  screen.hold(false);
  await settle();

  assert.equal(locks.length, 1);
  assert.equal(locks[0].released, true);
});

test('nothing is taken when not wanted, refused, or unsupported', async () => {
  const visible = () => true;
  const { ctx, api, locks } = makeWakeLock();
  ctx.createScreenWakeLock(api, visible).refresh();
  await settle();
  assert.equal(locks.length, 0);

  const refused = makeWakeLock({ refuse: true });
  const screen = refused.ctx.createScreenWakeLock(refused.api, visible);
  screen.hold(true);
  await settle();
  screen.hold(false);

  ctx.createScreenWakeLock(undefined, visible).hold(true);
});

function discardHarness(fails = {}) {
  const runs = [];
  const alerts = [];
  const ctx = loadScripts(
    [
      'workspace/files/ui/static/files/ui/js/file_operations.js',
      'workspace/common/static/ui/js/filesize.js',
      'workspace/files/ui/static/files/ui/js/upload_queue.js',
    ],
    {
      document: { addEventListener: () => {} },
      AppAlert: {
        error: (text) => alerts.push(`error: ${text}`),
        warning: (text) => alerts.push(`warning: ${text}`),
      },
    },
  );
  ctx.fileOperations = {
    run: async (kind, uuids) => {
      runs.push([kind, Array.from(uuids)]);
      const failed = fails[kind] || [];
      return {
        status: 'completed',
        errors: failed.map((uuid) => ({ uuid, message: 'Refused' })),
      };
    },
  };
  return { discard: ctx.discardFileUploads, runs, alerts };
}

const uploads = (...names) => names.map((name) => ({ uuid: `u-${name}`, name }));

test('discarding a batch runs one trash and one purge, whatever its size', async () => {
  const { discard, runs, alerts } = discardHarness();

  const discarded = await discard(uploads('a.jpg', 'b.jpg', 'c.jpg'));

  assert.deepEqual(Array.from(discarded), ['u-a.jpg', 'u-b.jpg', 'u-c.jpg']);
  assert.deepEqual(runs, [
    ['trash', ['u-a.jpg', 'u-b.jpg', 'u-c.jpg']],
    ['purge', ['u-a.jpg', 'u-b.jpg', 'u-c.jpg']],
  ]);
  assert.deepEqual(alerts, []);
});

test('only what reached the trash is purged, and each failure is reported once', async () => {
  const { discard, runs, alerts } = discardHarness({ trash: ['u-b.jpg', 'u-c.jpg'], purge: ['u-a.jpg'] });

  const discarded = await discard(uploads('a.jpg', 'b.jpg', 'c.jpg', 'd.jpg'));

  assert.deepEqual(Array.from(discarded), ['u-a.jpg', 'u-d.jpg']);
  assert.deepEqual(runs[1], ['purge', ['u-a.jpg', 'u-d.jpg']]);
  assert.deepEqual(alerts, [
    'error: Failed to discard 2 files',
    'warning: a.jpg was moved to trash but could not be permanently discarded',
  ]);
});

test('nothing trashed runs no purge', async () => {
  const { discard, runs, alerts } = discardHarness({ trash: ['u-a.jpg'] });

  assert.deepEqual(Array.from(await discard(uploads('a.jpg'))), []);
  assert.deepEqual(runs, [['trash', ['u-a.jpg']]]);
  assert.deepEqual(alerts, ['error: Failed to discard a.jpg']);
});
