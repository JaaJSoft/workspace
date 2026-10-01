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

test('toggleTagFilter adds then removes', () => {
  const table = makeTable();

  table.toggleTagFilter('tag-a');
  assert.deepStrictEqual(Array.from(table.tagFilter), ['tag-a']);
  assert.equal(table.hasTagFilter('tag-a'), true);

  table.toggleTagFilter('tag-b');
  assert.deepStrictEqual(Array.from(table.tagFilter), ['tag-a', 'tag-b']);

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

test('the filter badge counts the type and each tag, not the name query', () => {
  const table = makeTable();
  assert.equal(table.activeFilterCount(), 0);
  assert.equal(table.hasActiveFilters(), false);

  table.searchQuery = 'report';
  assert.equal(table.activeFilterCount(), 0);
  assert.equal(table.hasActiveFilters(), true);

  table.typeFilter = 'folders';
  table.tagFilter = ['tag-a', 'tag-b'];
  assert.equal(table.activeFilterCount(), 3);
  assert.equal(table.typeFilterLabel(), 'Folders');
  assert.equal(table.typeFilterIcon(), 'folder');
});

test('clearFilters drops the query, the type and the tags but keeps the sort', () => {
  const table = makeTable();
  table.searchQuery = 'report';
  table.typeFilter = 'favorites';
  table.tagFilter = ['tag-a'];
  table.sortField = 'size';
  table.sortDir = 'desc';

  table.clearFilters();

  assert.equal(table.searchQuery, '');
  assert.equal(table.typeFilter, 'all');
  assert.deepStrictEqual(Array.from(table.tagFilter), []);
  assert.equal(table.hasActiveFilters(), false);
  assert.equal(table.sortField, 'size');
  assert.equal(table.sortDir, 'desc');
});

test('picking the sort field in use flips the direction', () => {
  const table = makeTable();
  table.sortField = 'default';
  table.sortDir = 'asc';
  assert.equal(table.sortButtonLabel(), 'Sort');

  table.pickSort('name');
  assert.equal(table.sortField, 'name');
  assert.equal(table.sortDir, 'asc');
  assert.equal(table.sortButtonLabel(), 'Name');

  table.pickSort('name');
  assert.equal(table.sortDir, 'desc');

  table.pickSort('size');
  assert.equal(table.sortField, 'size');
  assert.equal(table.sortDir, 'desc');

  table.pickSort('default');
  table.pickSort('default');
  assert.equal(table.sortField, 'default');
  assert.equal(table.sortDir, 'desc');
});

test('the trash offers no favorite sort', () => {
  const table = makeTable();
  const ids = (isTrash) => Array.from(table.sortOptions(isTrash), (option) => option.id);
  assert.ok(ids(false).includes('favorite'));
  assert.ok(!ids(true).includes('favorite'));
  assert.deepStrictEqual(ids(true), ['default', 'name', 'size', 'created', 'modified', 'type']);
});
