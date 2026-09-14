'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// The public drop zone lives outside the #shared-content swap region, so a
// finished batch has to ask folderNav to re-fetch the listing above it.
// One request per queued file: reports half the bytes, then answers with the
// next status in the list.
class FakeXhr {
  constructor() {
    this.upload = {};
  }
  open() {}
  setRequestHeader() {}
  send() {
    setTimeout(() => {
      this.upload.onprogress({ lengthComputable: true, loaded: 50, total: 100 });
      this.status = FakeXhr.statuses.shift();
      this.onload();
    }, 0);
  }
}

function makeDrop(statuses, dataset) {
  const reloads = [];
  const fields = [];
  const nodes = [];
  FakeXhr.statuses = statuses;
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/shared_dropzone.js', {
    URLSearchParams,
    FormData: class {
      append(key, value) {
        fields.push(key);
        if (key === 'node') nodes.push(value);
      }
    },
    XMLHttpRequest: FakeXhr,
    setTimeout,
    folderNav: { reload: () => reloads.push(1) },
    document: { getElementById: () => (dataset ? { dataset } : null) },
  });
  const drop = ctx.sharedDrop('tok', '', 0, 'Inbox');
  drop.init();
  return { drop, reloads, fields, nodes };
}

test('a batch with a successful upload refreshes the listing once', async () => {
  const { drop, reloads } = makeDrop([204, 204]);
  drop.queue.push(
    { file: { size: 1 }, name: 'a.txt', state: 'pending' },
    { file: { size: 1 }, name: 'b.txt', state: 'pending' }
  );
  await drop.sendAll();
  assert.equal(drop.doneCount(), 2);
  assert.equal(reloads.length, 1);
});

test('a batch where nothing landed leaves the listing alone', async () => {
  const { drop, reloads } = makeDrop([400]);
  drop.queue.push({ file: { size: 1 }, name: 'a.txt', state: 'pending' });
  await drop.sendAll();
  assert.equal(drop.doneCount(), 0);
  assert.equal(reloads.length, 0);
});

test('the zone names and targets the browsed folder', async () => {
  const { drop, fields } = makeDrop([204], { node: 'abc', nodeName: 'Sub' });
  assert.equal(drop.targetName, 'Sub');
  drop.queue.push(drop.queued({ size: 1, name: 'a.txt' }));
  await drop.sendAll();
  assert.deepStrictEqual(fields, ['file', 'node']);
});

test('a file keeps the folder it was dropped into if the visitor navigates mid-batch', async () => {
  const dataset = { node: 'abc', nodeName: 'Sub' };
  const { drop, nodes } = makeDrop([204, 204], dataset);
  drop.queue.push(drop.queued({ size: 1, name: 'a.txt' }), drop.queued({ size: 1, name: 'b.txt' }));
  dataset.node = 'xyz';
  dataset.nodeName = 'Other';
  drop.syncTarget();
  await drop.sendAll();
  assert.deepStrictEqual(nodes, ['abc', 'abc']);
});

test('without a browsed folder the zone names the root and sends no node', async () => {
  const { drop, fields } = makeDrop([204]);
  assert.equal(drop.targetName, 'Inbox');
  drop.queue.push(drop.queued({ size: 1, name: 'a.txt' }));
  await drop.sendAll();
  assert.deepStrictEqual(fields, ['file']);
});

test('a file shows the bytes sent while it is on the wire', async () => {
  const { drop } = makeDrop([204]);
  const item = drop.queued({ size: 1, name: 'a.txt' });
  drop.queue.push(item);
  let seen = null;
  const original = FakeXhr.prototype.send;
  FakeXhr.prototype.send = function () {
    setTimeout(() => {
      this.upload.onprogress({ lengthComputable: true, loaded: 50, total: 100 });
      seen = { state: item.state, percent: item.percent };
      this.status = 204;
      this.onload();
    }, 0);
  };
  try {
    await drop.sendAll();
  } finally {
    FakeXhr.prototype.send = original;
  }
  assert.deepStrictEqual(seen, { state: 'sending', percent: 50 });
  assert.equal(item.state, 'done');
  assert.equal(drop.sendingCount(), 0);
});
