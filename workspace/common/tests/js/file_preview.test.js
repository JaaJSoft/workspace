// <file-preview> (file_preview.js): the batch URL a set of previews is
// asked for with. The element itself needs a DOM and Alpine; the files and
// notes e2e suites drive it in a browser.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript, CUSTOM_ELEMENT_STUBS } = require('./loader');

const ctx = loadScript('workspace/common/static/ui/js/file_preview.js', {
  ...CUSTOM_ELEMENT_STUBS,
  document: { addEventListener() {} },
  URLSearchParams,
});
const { batchUrl, BATCH_SIZE } = ctx.filePreview;

test('one batch names its files and their slots in the same order', () => {
  const url = batchUrl('/files/previews', [
    { uuid: 'a1', slot: 'file-preview-1' },
    { uuid: 'b2', slot: 'file-preview-2' },
  ]);
  const params = new URL(url, 'https://x').searchParams;
  assert.equal(new URL(url, 'https://x').pathname, '/files/previews');
  assert.equal(params.get('uuids'), 'a1,b2');
  assert.equal(params.get('targets'), 'file-preview-1,file-preview-2');
});

test('an endpoint with its own query keeps it', () => {
  const url = batchUrl('/x/previews?scope=a', [{ uuid: 'a1', slot: 'file-preview-1' }]);
  const params = new URL(url, 'https://x').searchParams;
  assert.equal(params.get('scope'), 'a');
  assert.equal(params.get('uuids'), 'a1');
});

test('a batch stays under the server cap', () => {
  // files/ui/viewers.py MAX_PREVIEWS
  assert.ok(BATCH_SIZE <= 100);
});
