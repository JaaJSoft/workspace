'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// POST /api/v1/files/actions answers a catalogue of distinct actions and,
// per file, the keys that apply. Every consumer wants { uuid: [action] },
// so the expansion lives in one place.
function load(fetchImpl) {
  return loadScript('workspace/files/ui/static/files/ui/js/file_actions.js', {
    document: { getElementById: () => null },
    getCSRFToken: () => 'token',
    fetch: fetchImpl,
  });
}

const PAYLOAD = {
  actions: {
    download: { id: 'download', label: 'Download' },
    toggle_favorite: { id: 'toggle_favorite', label: 'Add to favorites', state: { is_favorite: false } },
    'toggle_favorite#2': { id: 'toggle_favorite', label: 'Remove from favorites', state: { is_favorite: true } },
  },
  files: {
    plain: ['download', 'toggle_favorite'],
    starred: ['download', 'toggle_favorite#2'],
    stale: ['download', 'vanished'],
  },
};

test('expandActions resolves each file\'s keys against the catalogue', () => {
  const ctx = load(() => { throw new Error('no fetch expected'); });
  const expanded = ctx.fileActions.expandActions(PAYLOAD);
  assert.deepStrictEqual(Object.keys(expanded).sort(), ['plain', 'stale', 'starred']);
  assert.deepStrictEqual(Array.from(expanded.plain).map((a) => a.label), ['Download', 'Add to favorites']);
  assert.deepStrictEqual(Array.from(expanded.starred).map((a) => a.label), ['Download', 'Remove from favorites']);
  // A variant keeps the action id the consumers switch on.
  assert.equal(expanded.starred[1].id, 'toggle_favorite');
  assert.equal(expanded.starred[1].state.is_favorite, true);
  // A key the catalogue does not carry is dropped, not surfaced as undefined.
  assert.deepStrictEqual(Array.from(expanded.stale).map((a) => a.id), ['download']);
});

test('expandActions tolerates an empty answer', () => {
  const ctx = load(() => { throw new Error('no fetch expected'); });
  assert.deepStrictEqual({ ...ctx.fileActions.expandActions({}) }, {});
});

test('fetchActions posts the uuids and hands back the expanded map', async () => {
  const calls = [];
  const ctx = load(async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => PAYLOAD };
  });
  const result = await ctx.fileActions.fetchActions(['plain', 'starred']);
  assert.equal(calls[0].url, '/api/v1/files/actions');
  assert.equal(calls[0].options.method, 'POST');
  assert.deepStrictEqual({ ...JSON.parse(calls[0].options.body) }, { uuids: ['plain', 'starred'] });
  assert.equal(calls[0].options.headers['X-CSRFToken'], 'token');
  assert.equal(result.plain.length, 2);
});

test('fetchActions resolves to null when the server refuses', async () => {
  const ctx = load(async () => ({ ok: false, status: 400, json: async () => ({ detail: 'nope' }) }));
  assert.equal(await ctx.fileActions.fetchActions(['plain']), null);
});
