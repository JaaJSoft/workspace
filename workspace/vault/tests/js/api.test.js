// The URLs and methods the vault client sends. The endpoints themselves are
// covered by the Django suite; what is pinned here is the shape of the call,
// because a wrong method or a missing token fails at runtime with a status
// code that says nothing about which of the two it was.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

function withFetch() {
  const calls = [];
  const fetchStub = (url, options) => {
    calls.push({ url, options });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) });
  };
  const ctx = loadScript('workspace/vault/ui/static/vault/ui/js/api.js', {
    fetch: fetchStub,
    getCSRFToken: () => 'token',
  });
  return { ctx, calls, api: ctx.vaultApi };
}

const VAULT = '018f3f6e-0000-7000-8000-00000000000b';
const ENTRY = '018f3f6e-0000-7000-8000-00000000000a';
const FOLDER = '018f3f6e-0000-7000-8000-000000000001';

test('listEntries encodes its vault filter', async () => {
  const { api, calls } = withFetch();
  await api.listEntries(VAULT);
  assert.equal(calls[0].url, `/api/v1/vault/entries?vault=${VAULT}`);
});

test('listEntries asks for the trash only when told to', async () => {
  const { api, calls } = withFetch();
  await api.listEntries(VAULT);
  assert.ok(!calls[0].url.includes('trashed'));
  await api.listEntries(VAULT, { trashed: true });
  assert.ok(calls[1].url.endsWith('&trashed=true'));
});

test('listFolders and listTags scope themselves to one vault', async () => {
  const { api, calls } = withFetch();
  await api.listFolders(VAULT);
  await api.listTags(VAULT);
  assert.equal(calls[0].url, `/api/v1/vault/folders?vault=${VAULT}`);
  assert.equal(calls[1].url, `/api/v1/vault/tags?vault=${VAULT}`);
});

test('deleteFolder posts its entries rather than issuing a DELETE', () => {
  const { api, calls } = withFetch();
  api.deleteFolder(FOLDER, []);
  assert.equal(calls[0].options.method, 'POST');
  assert.ok(calls[0].url.endsWith('/delete'));
  assert.deepStrictEqual(JSON.parse(calls[0].options.body), { entries: [] });
});

test('updateEntry replaces rather than patches', () => {
  const { api, calls } = withFetch();
  api.updateEntry(ENTRY, { uuid: ENTRY });
  // PUT, because the signature covers every field: a partial write would
  // store a signature over values the row no longer holds.
  assert.equal(calls[0].options.method, 'PUT');
});

test('trashEntry deletes the member, which the server takes as a soft delete', () => {
  const { api, calls } = withFetch();
  api.trashEntry(ENTRY);
  assert.equal(calls[0].options.method, 'DELETE');
  assert.equal(calls[0].url, `/api/v1/vault/entries/${ENTRY}`);
});

test('every unsafe call carries the CSRF token, and no read does', () => {
  const { api, calls } = withFetch();
  api.createEntry({});
  api.updateEntry(ENTRY, {});
  api.trashEntry(ENTRY);
  api.createFolder({});
  api.updateFolder(FOLDER, {});
  api.deleteFolder(FOLDER, []);
  api.createTag({});
  api.updateTag(FOLDER, {});
  api.deleteTag(FOLDER);
  assert.equal(calls.length, 9);
  for (const call of calls) {
    assert.notEqual(call.options.method, 'GET');
    assert.equal(call.options.headers['X-CSRFToken'], 'token');
  }
  api.getEntry(ENTRY);
  assert.equal(calls[9].options.headers['X-CSRFToken'], undefined);
});
