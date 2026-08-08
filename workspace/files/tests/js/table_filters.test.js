'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function makeTable() {
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/table.js', {
    _filePrefsCache: {},
    document: { createDocumentFragment: () => ({ appendChild() {} }) },
  });
  return ctx.fileTableWithView();
}

function makeCard(dataset) {
  return { dataset, style: {} };
}

test('no tag filter keeps every row', () => {
  const table = makeTable();
  assert.equal(table.matchesTagFilter(''), true);
  assert.equal(table.matchesTagFilter('tag-a tag-b'), true);
});

test('tag filter matches any of the selected tags', () => {
  const table = makeTable();
  table.tagFilter = ['tag-a', 'tag-c'];

  assert.equal(table.matchesTagFilter('tag-a '), true);
  assert.equal(table.matchesTagFilter('tag-b tag-c '), true);
  assert.equal(table.matchesTagFilter('tag-b '), false);
  assert.equal(table.matchesTagFilter(''), false);
  assert.equal(table.matchesTagFilter(undefined), false);
});

test('toggleTagFilter adds then removes, and reports a label', () => {
  const table = makeTable();

  assert.equal(table.tagFilterLabel(), 'Tags');
  table.toggleTagFilter('tag-a');
  assert.deepStrictEqual(Array.from(table.tagFilter), ['tag-a']);
  assert.equal(table.hasTagFilter('tag-a'), true);
  assert.equal(table.tagFilterLabel(), '1 tag');

  table.toggleTagFilter('tag-b');
  assert.equal(table.tagFilterLabel(), '2 tags');

  table.toggleTagFilter('tag-a');
  assert.deepStrictEqual(Array.from(table.tagFilter), ['tag-b']);

  table.clearTagFilter();
  assert.deepStrictEqual(Array.from(table.tagFilter), []);
});

test('rows and mosaic cards agree on every filter', () => {
  const table = makeTable();
  const tagged = makeCard({ name: 'report.txt', nodeType: 'file', tags: 'tag-a ' });
  const untagged = makeCard({ name: 'notes.txt', nodeType: 'file', tags: ' ' });
  table.tagFilter = ['tag-a'];

  assert.equal(table.shouldShowCard(tagged), table.matchesFilter(tagged, ''));
  assert.equal(table.shouldShowCard(tagged), true);
  assert.equal(table.shouldShowCard(untagged), table.matchesFilter(untagged, ''));
  assert.equal(table.shouldShowCard(untagged), false);
});

test('applyCards hides the cards that do not match', () => {
  /* Regression: mosaic cards were filtered by an `x-show` binding that only
     ever ran at mount, so search/type/tag filters silently applied to the
     list view alone. Visibility is now driven from applyCards(). */
  const table = makeTable();
  const cards = [
    makeCard({ name: 'report.txt', nodeType: 'file', tags: 'tag-a ' }),
    makeCard({ name: 'notes.txt', nodeType: 'file', tags: 'tag-b ' }),
    makeCard({ name: 'Archive', nodeType: 'folder', tags: ' ' }),
  ];
  table.$el = { querySelectorAll: () => cards };

  table.tagFilter = ['tag-b'];
  table.applyCards();
  assert.deepStrictEqual(
    cards.map((c) => c.style.display),
    ['none', '', 'none']
  );

  table.clearTagFilter();
  table.typeFilter = 'folders';
  table.applyCards();
  assert.deepStrictEqual(
    cards.map((c) => c.style.display),
    ['none', 'none', '']
  );
});
