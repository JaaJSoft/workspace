'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function makeMixin(extra = {}) {
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/tags.js', {
    getCSRFToken: () => 'token',
    document: { getElementById: () => null },
    ...extra,
  });
  const mixin = ctx.tagsMixin();
  mixin.allTags = [
    { uuid: 'b', name: 'beta', is_favorite: false, file_count: 0 },
    { uuid: 'a', name: 'Alpha', is_favorite: true, file_count: 3 },
    { uuid: 'c', name: 'Gamma', is_favorite: true, file_count: 0 },
    { uuid: 'd', name: 'delta', is_favorite: false, file_count: 1 },
  ];
  return mixin;
}

test('favoriteTags keeps only the favourites, sorted by name', () => {
  const m = makeMixin();
  assert.deepStrictEqual(m.favoriteTags().map((t) => t.uuid), ['a', 'c']);
});

test('filteredManagedTags lists favourites first, then by name, case-insensitively', () => {
  const m = makeMixin();
  assert.deepStrictEqual(m.filteredManagedTags().map((t) => t.uuid), ['a', 'c', 'b', 'd']);
});

test('filteredManagedTags narrows on the query without touching the order', () => {
  const m = makeMixin();
  m.tagManager.query = '  A ';
  assert.deepStrictEqual(m.filteredManagedTags().map((t) => t.uuid), ['a', 'c', 'b', 'd']);
  m.tagManager.query = 'ELT';
  assert.deepStrictEqual(m.filteredManagedTags().map((t) => t.uuid), ['d']);
});

test('unusedTagCount counts the tags with no file', () => {
  const m = makeMixin();
  assert.equal(m.unusedTagCount(), 2);
});

test('tagMergeTargets excludes the tag being merged', () => {
  const m = makeMixin();
  m.tagManager.mergeSource = m.allTags[1];
  assert.deepStrictEqual(m.tagMergeTargets().map((t) => t.uuid), ['c', 'b', 'd']);
});

test('deleteTag drops the tag locally and announces the change', async () => {
  const calls = [];
  const events = [];
  const m = makeMixin({
    fetch: async (url, opts) => {
      calls.push([url, opts.method]);
      return { ok: true, status: 204 };
    },
    confirm: () => true,
    CustomEvent: class { constructor(type) { this.type = type; } },
    dispatchEvent: (e) => events.push(e.type),
  });
  await m.deleteTag(m.allTags[0]);
  assert.deepStrictEqual(calls, [['/api/v1/tags/b', 'DELETE']]);
  assert.deepStrictEqual(m.allTags.map((t) => t.uuid), ['a', 'c', 'd']);
  assert.deepStrictEqual(events, ['tags-changed']);
});

test('confirmTagMerge replaces both tags with the merged one the server returns', async () => {
  const bodies = [];
  const m = makeMixin({
    fetch: async (url, opts) => {
      bodies.push([url, JSON.parse(opts.body)]);
      return { ok: true, json: async () => ({ uuid: 'a', name: 'Alpha', is_favorite: true, file_count: 4 }) };
    },
    CustomEvent: class { constructor(type) { this.type = type; } },
    dispatchEvent: () => {},
  });
  m.tagManager.mergeSource = m.allTags[3];
  m.tagManager.mergeTarget = 'a';
  await m.confirmTagMerge();
  assert.deepStrictEqual(bodies, [['/api/v1/tags/d/merge', { into: 'a' }]]);
  assert.deepStrictEqual(m.allTags.map((t) => t.uuid), ['b', 'a', 'c']);
  assert.equal(m.allTags.find((t) => t.uuid === 'a').file_count, 4);
  assert.equal(m.tagManager.mergeSource, null);
});
