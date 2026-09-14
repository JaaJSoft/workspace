'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// The public drop zone lives outside the #shared-content swap region, so a
// finished batch has to ask folderNav to re-fetch the listing above it.
function makeDrop(statuses, dataset) {
  const reloads = [];
  const fields = [];
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/shared_dropzone.js', {
    URLSearchParams,
    FormData: class {
      append(key, value) {
        fields.push(key);
      }
    },
    fetch: async () => ({ status: statuses.shift() }),
    folderNav: { reload: () => reloads.push(1) },
    document: { getElementById: () => (dataset ? { dataset } : null) },
  });
  const drop = ctx.sharedDrop('tok', '', 0, 'Inbox');
  drop.init();
  return { drop, reloads, fields };
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
  drop.queue.push({ file: { size: 1 }, name: 'a.txt', state: 'pending' });
  await drop.sendAll();
  assert.deepStrictEqual(fields, ['file', 'node']);
});

test('without a browsed folder the zone names the root and sends no node', async () => {
  const { drop, fields } = makeDrop([204]);
  assert.equal(drop.targetName, 'Inbox');
  drop.queue.push({ file: { size: 1 }, name: 'a.txt', state: 'pending' });
  await drop.sendAll();
  assert.deepStrictEqual(fields, ['file']);
});
