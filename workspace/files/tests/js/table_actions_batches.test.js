'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// The actions endpoint refuses more than 200 UUIDs per call. A listing
// larger than that used to get a 400 back and no actions at all: no
// context menu, no favourite toggle, no bulk bar. The fetch has to slice.
function makeTable(fetchCalls) {
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/table.js', {
    _filePrefsCache: {},
    document: { createDocumentFragment: () => ({ appendChild() {} }) },
    getCSRFToken: () => 'token',
    fetch: async (url, options) => {
      const { uuids } = JSON.parse(options.body);
      fetchCalls.push({ url, uuids });
      return {
        ok: true,
        json: async () => Object.fromEntries(uuids.map((uuid) => [uuid, [{ id: 'rename', bulk: false }]])),
      };
    },
  });
  return ctx.fileTableWithView();
}

test('fetchActions asks in slices of 200 and merges the answers', async () => {
  const fetchCalls = [];
  const table = makeTable(fetchCalls);
  const uuids = Array.from({ length: 450 }, (_, i) => `uuid-${i}`);
  table.originalRows = uuids.map((uuid) => ({ dataset: { uuid } }));

  await table.fetchActions();

  assert.deepStrictEqual(fetchCalls.map((c) => c.uuids.length), [200, 200, 50]);
  assert.ok(fetchCalls.every((c) => c.url === '/api/v1/files/actions'));
  assert.deepStrictEqual(Array.from(fetchCalls.flatMap((c) => c.uuids)), uuids);
  assert.equal(Object.keys(table.actionsMap).length, 450);
  assert.equal(table.actionsMap['uuid-449'][0].id, 'rename');
  assert.equal(table.actionsLoading, false);
});

test('fetchActions still sends one request for a small listing', async () => {
  const fetchCalls = [];
  const table = makeTable(fetchCalls);
  table.originalRows = [{ dataset: { uuid: 'a' } }, { dataset: { uuid: 'b' } }];

  await table.fetchActions();

  assert.deepStrictEqual(fetchCalls.map((c) => Array.from(c.uuids)), [['a', 'b']]);
});
