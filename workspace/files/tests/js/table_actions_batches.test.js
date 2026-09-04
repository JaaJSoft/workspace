'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

// The actions endpoint refuses more than 200 UUIDs per call. A listing
// larger than that used to get a 400 back and no actions at all: no
// context menu, no favourite toggle, no bulk bar. The fetch has to slice.
function makeTable(fetchCalls, { deferred = false, failing = [] } = {}) {
  const ctx = loadScripts([
    'workspace/files/ui/static/files/ui/js/file_actions.js',
    'workspace/files/ui/static/files/ui/js/table.js',
  ], {
    _filePrefsCache: {},
    document: { createDocumentFragment: () => ({ appendChild() {} }), getElementById: () => null },
    getCSRFToken: () => 'token',
    fetch: (url, options) => {
      const { uuids } = JSON.parse(options.body);
      const call = { url, uuids };
      // The wire shape: one catalogue, and per file the keys that apply.
      const response = {
        ok: true,
        json: async () => ({
          actions: { rename: { id: 'rename', bulk: false } },
          files: Object.fromEntries(uuids.map((uuid) => [uuid, ['rename']])),
        }),
      };
      let promise;
      if (failing.includes(fetchCalls.length)) {
        promise = Promise.reject(new TypeError('Failed to fetch'));
      } else if (deferred) {
        promise = new Promise((resolve) => { call.resolve = () => resolve(response); });
      } else {
        promise = Promise.resolve(response);
      }
      fetchCalls.push(call);
      return promise;
    },
  });
  return ctx.fileTableWithView();
}

function rows(count) {
  return Array.from({ length: count }, (_, i) => ({ dataset: { uuid: `uuid-${i}` } }));
}

test('fetchActions asks in slices of 200 and merges the answers', async () => {
  const fetchCalls = [];
  const table = makeTable(fetchCalls);
  table.originalRows = rows(450);

  await table.fetchActions();

  assert.deepStrictEqual(fetchCalls.map((c) => c.uuids.length), [200, 200, 50]);
  assert.ok(fetchCalls.every((c) => c.url === '/api/v1/files/actions'));
  assert.deepStrictEqual(Array.from(fetchCalls.flatMap((c) => c.uuids)), table.originalRows.map((r) => r.dataset.uuid));
  assert.equal(Object.keys(table.actionsMap).length, 450);
  assert.equal(table.actionsMap['uuid-449'][0].id, 'rename');
  assert.equal(table.actionsLoading, false);
});

test('fetchActions exposes each slice as it lands, without waiting for the others', async () => {
  const fetchCalls = [];
  const table = makeTable(fetchCalls, { deferred: true });
  table.originalRows = rows(450);

  const done = table.fetchActions();
  assert.equal(fetchCalls.length, 3);

  fetchCalls[1].resolve();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(Object.keys(table.actionsMap).length, 200);
  assert.ok(table.actionsMap['uuid-200']);
  assert.equal(table.actionsMap['uuid-0'], undefined);
  assert.equal(table.actionsLoading, true);

  fetchCalls[0].resolve();
  fetchCalls[2].resolve();
  await done;
  assert.equal(Object.keys(table.actionsMap).length, 450);
  assert.equal(table.actionsLoading, false);
});

test('a slice lost to the network does not cost the other slices their answers', async () => {
  const fetchCalls = [];
  const table = makeTable(fetchCalls, { failing: [1] });
  table.originalRows = rows(450);

  await table.fetchActions();

  assert.equal(Object.keys(table.actionsMap).length, 250);
  assert.ok(table.actionsMap['uuid-0']);
  assert.equal(table.actionsMap['uuid-200'], undefined);
  assert.ok(table.actionsMap['uuid-449']);
  assert.equal(table.actionsLoading, false);
});

test('fetchActions still sends one request for a small listing', async () => {
  const fetchCalls = [];
  const table = makeTable(fetchCalls);
  table.originalRows = [{ dataset: { uuid: 'a' } }, { dataset: { uuid: 'b' } }];

  await table.fetchActions();

  assert.deepStrictEqual(fetchCalls.map((c) => Array.from(c.uuids)), [['a', 'b']]);
});
