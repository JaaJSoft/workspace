// The storage analysis dialog mirrors the scope it shows into the page URL
// (storage.js syncUrl), so a reload reopens it on the same folder.
const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

const FOLDER = '0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b';

function analysis() {
  const writes = [];
  const ctx = loadScripts(
    ['workspace/common/static/ui/js/uuid.js', 'workspace/files/ui/static/files/ui/js/storage.js'],
    {
      URL,
      location: { href: 'https://ws.test/files?view=list', origin: 'https://ws.test' },
      navHistory: { replace: (url) => writes.push(url), state: () => null },
    },
  );
  return { component: ctx.storageAnalysis(), writes };
}

test('a folder scope is written with its uuid and filters', () => {
  const { component, writes } = analysis();
  component.syncUrl(`/files/storage/${FOLDER}?category=images&q=raw`);
  assert.equal(writes.length, 1);
  const params = new URL(writes[0]).searchParams;
  assert.equal(params.get('storage'), FOLDER);
  assert.equal(params.get('storage_category'), 'images');
  assert.equal(params.get('storage_q'), 'raw');
  assert.equal(params.get('view'), 'list');
});

test('the root scope is written as root', () => {
  const { component, writes } = analysis();
  component.syncUrl('/files/storage');
  assert.equal(new URL(writes[0]).searchParams.get('storage'), 'root');
});

test('any other URL is left out of the page URL', () => {
  for (const url of ['/files/elsewhere', '/files/storage/not-a-folder', `/files/storage/${FOLDER}/x`]) {
    const { component, writes } = analysis();
    component.syncUrl(url);
    assert.deepEqual(writes, [], url);
  }
});
