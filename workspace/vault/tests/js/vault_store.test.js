// What belongs on screen, decided without a browser, a key or a server.
//
// The store is the half of the browser that can be tested cheaply, and the
// half where a mistake is silent: a filter that quietly drops a row looks
// exactly like a vault that does not hold it.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const FOLDERS = [
  { uuid: 'f-bank', parent: null, name: 'Banque' },
  { uuid: 'f-savings', parent: 'f-bank', name: 'Épargne' },
  { uuid: 'f-insurance', parent: null, name: 'Assurances' },
];

const TAGS = [
  { uuid: 't-home', name: 'perso', color: '#22c55e' },
  { uuid: 't-work', name: 'pro', color: '#3b82f6' },
];

const ENTRIES = [
  { uuid: 'e-1', folder: 'f-bank', name: 'Compte courant', username: 'jc@example.fr', tags: ['t-home'], favorite: true, trashed: false, modified: '2026-08-27' },
  { uuid: 'e-2', folder: 'f-savings', name: 'Livret A', username: '1234567', tags: ['t-home'], favorite: false, trashed: false, modified: '2026-08-03' },
  { uuid: 'e-3', folder: null, name: 'Streaming', username: 'famille@example.fr', tags: ['t-work'], favorite: true, trashed: false, modified: '2026-08-20' },
  { uuid: 'e-4', folder: null, name: 'Mutuelle', username: 'jc@example.fr', tags: [], favorite: false, trashed: true, modified: '2026-08-25' },
];

function store(overrides = {}) {
  const ctx = loadScript('workspace/vault/ui/static/vault/ui/js/vault_store.js');
  const instance = ctx.vaultStore();
  instance.setData({ folders: FOLDERS, tags: TAGS, entries: ENTRIES, ...overrides });
  return instance;
}

const names = (rows) => Array.from(rows.map((row) => row.name));

test('the vault root shows its own folders and its loose entries', () => {
  const s = store();
  assert.deepStrictEqual(names(s.visibleFolders()), ['Banque', 'Assurances']);
  assert.deepStrictEqual(names(s.visibleEntries()), ['Streaming']);
});

test('a folder shows its direct children only', () => {
  const s = store();
  s.openFolder('f-bank');
  assert.deepStrictEqual(names(s.visibleFolders()), ['Épargne']);
  assert.deepStrictEqual(names(s.visibleEntries()), ['Compte courant']);
});

test('the trash holds entries and never a folder', () => {
  // Deleting a folder is immediate and composite, so nothing lands here.
  const s = store();
  s.setView('trash');
  assert.deepStrictEqual(names(s.visibleEntries()), ['Mutuelle']);
  assert.deepStrictEqual(names(s.visibleFolders()), []);
});

test('a trashed entry stays out of every other view', () => {
  const s = store();
  assert.ok(!names(s.visibleEntries()).includes('Mutuelle'));
  s.setView('favorites');
  assert.ok(!names(s.visibleEntries()).includes('Mutuelle'));
  s.setTagFilter('t-home');
  assert.ok(!names(s.visibleEntries()).includes('Mutuelle'));
});

test('favourites ignore the folder the user happens to be in', () => {
  const s = store();
  s.setView('favorites');
  assert.deepStrictEqual(names(s.visibleEntries()), ['Compte courant', 'Streaming']);
});

test('a tag filter cuts across the tree', () => {
  const s = store();
  s.setTagFilter('t-home');
  assert.deepStrictEqual(names(s.visibleEntries()), ['Compte courant', 'Livret A']);
  assert.deepStrictEqual(names(s.visibleFolders()), []);
});

test('clicking the active tag clears the filter', () => {
  const s = store();
  s.setTagFilter('t-home');
  s.setTagFilter('t-home');
  assert.equal(s.tagFilter, null);
});

test('the search reads the login as well as the name', () => {
  // Someone hunting for an account types the address they know, not the
  // label they gave it.
  const s = store();
  s.search = 'famille@';
  assert.deepStrictEqual(names(s.visibleEntries()), ['Streaming']);
});

test('the search also narrows folders', () => {
  const s = store();
  s.search = 'ban';
  assert.deepStrictEqual(names(s.visibleFolders()), ['Banque']);
});

test('the type filter can hide either kind', () => {
  const s = store();
  s.typeFilter = 'folders';
  assert.deepStrictEqual(names(s.visibleEntries()), []);
  s.typeFilter = 'entries';
  assert.deepStrictEqual(names(s.visibleFolders()), []);
});

test('sorting by name in reverse orders what is on screen', () => {
  const s = store();
  s.setView('favorites');
  s.sortField = 'name';
  s.toggleSortDir();
  assert.deepStrictEqual(names(s.visibleEntries()), ['Streaming', 'Compte courant']);
});

test('sorting never reorders the array it was handed', () => {
  // Asked of `sorted` directly, because the listing path hands it an array
  // `filter` has already copied - so a sort in place there would be
  // invisible, and this contract would go untested until the first caller
  // passed the store's own rows.
  const s = store();
  s.sortField = 'name';
  const rows = s.entries;
  s.sorted(rows);
  assert.deepStrictEqual(
    Array.from(rows.map((entry) => entry.uuid)),
    ['e-1', 'e-2', 'e-3', 'e-4'],
  );
});

test('the default sort leaves the server order alone', () => {
  const s = store();
  s.setView('favorites');
  assert.deepStrictEqual(names(s.visibleEntries()), ['Compte courant', 'Streaming']);
});

test('going back then somewhere else drops the future', () => {
  const s = store();
  s.openFolder('f-bank');
  s.openFolder('f-savings');
  s.goBack();
  assert.equal(s.folderUuid, 'f-bank');
  assert.ok(s.canGoForward());

  s.openFolder('f-insurance');
  assert.ok(!s.canGoForward());
  assert.equal(s.folderUuid, 'f-insurance');
  // The assertion that bites: with the abandoned branch still in the stack,
  // the cursor sits at the end all the same, and only *back* reveals it -
  // landing on the folder the user turned away from.
  s.goBack();
  assert.equal(s.folderUuid, 'f-bank');
});

test('up walks the tree, not the history', () => {
  // The distinction matters: arriving in a nested folder from a tag filter
  // still has a parent to climb to, and no back entry that means the same.
  const s = store();
  s.openFolder('f-savings');
  s.goUp();
  assert.equal(s.folderUuid, 'f-bank');
  s.goUp();
  assert.equal(s.folderUuid, null);
  assert.ok(!s.canGoUp());
});

test('up is offered only where there is a tree to climb', () => {
  const s = store();
  s.setView('trash');
  assert.ok(!s.canGoUp());
  s.setTagFilter('t-home');
  assert.ok(!s.canGoUp());
});

test('the breadcrumb names the vault, then every ancestor', () => {
  const s = store();
  s.openFolder('f-savings');
  assert.deepStrictEqual(names(s.breadcrumbs('Personnel')), [
    'Personnel',
    'Banque',
    'Épargne',
  ]);
});

test('the breadcrumb stops at the vault outside the tree', () => {
  const s = store();
  s.setView('trash');
  assert.deepStrictEqual(names(s.breadcrumbs('Personnel')), ['Personnel']);
});

test('navigating clears the selection', () => {
  // Acting on rows that scrolled away is the shape of an accidental bulk
  // delete.
  const s = store();
  s.toggleSelection('e-3');
  s.openFolder('f-bank');
  assert.deepStrictEqual(Array.from(s.selected), []);
});

test('selection toggles and reads back as rows', () => {
  const s = store();
  s.toggleSelection('e-1');
  s.toggleSelection('e-2');
  s.toggleSelection('e-1');
  assert.deepStrictEqual(names(s.selectedEntries()), ['Livret A']);
});

test('the status line counts what is on screen', () => {
  const s = store();
  assert.equal(s.statusLine(), '3 items · 2 folders · 1 entry');
  s.openFolder('f-bank');
  assert.equal(s.statusLine(), '2 items · 1 folder · 1 entry');
});

test('an empty result tells a filtered view apart from an empty one', () => {
  // The two deserve different words on screen: "this folder is empty" and
  // "nothing matches" send the user to different places.
  const s = store();
  s.openFolder('f-insurance');
  assert.ok(s.isEmpty());
  assert.ok(!s.filtering());
  s.search = 'zzz';
  assert.ok(s.isEmpty());
  assert.ok(s.filtering());
});

test('reset clears the filters and leaves the location alone', () => {
  const s = store();
  s.openFolder('f-bank');
  s.search = 'x';
  s.typeFilter = 'entries';
  s.sortField = 'name';
  s.resetAll();
  assert.equal(s.search, '');
  assert.equal(s.typeFilter, 'all');
  assert.equal(s.folderUuid, 'f-bank');
});

test('the tag count ignores the trash', () => {
  const s = store();
  assert.equal(s.tagCount('t-home'), 2);
  assert.equal(s.trashCount(), 1);
});

test('tampered rows are counted, never listed', () => {
  // The count is all the browser may say about them: rendering any part of a
  // row whose signature failed is showing unverified data.
  const s = store({ tamperedCount: 2 });
  assert.equal(s.tamperedCount, 2);
  assert.equal(s.visibleEntries().length + s.visibleFolders().length, 3);
});

test('select-all covers what is on screen and nothing else', () => {
  const s = store();
  assert.equal(s.selectAllState(), 'none');
  s.toggleSelectAll();
  // The vault root holds one loose entry; the ones inside folders and the one
  // in the trash are not in this listing, so "all" must not have taken them.
  assert.deepStrictEqual(Array.from(s.selected), ['e-3']);
  assert.equal(s.selectAllState(), 'all');
  s.toggleSelectAll();
  assert.deepStrictEqual(Array.from(s.selected), []);
});

test('select-all reports partial once one row of several is picked', () => {
  const s = store();
  s.setView('favorites');
  s.toggleSelection('e-1');
  assert.equal(s.selectAllState(), 'partial');
  s.toggleSelectAll();
  assert.deepStrictEqual(Array.from(s.selected).sort(), ['e-1', 'e-3']);
  assert.equal(s.selectAllState(), 'all');
});

test('unticking select-all leaves a selection made outside the listing alone', () => {
  const s = store();
  s.selected = ['e-3', 'e-4'];
  s.toggleSelectAll();
  assert.deepStrictEqual(Array.from(s.selected), ['e-4']);
});

test('an empty listing is never "all selected"', () => {
  // Otherwise the header box ticks itself on a folder holding nothing.
  const s = store();
  s.search = 'nothing matches this';
  assert.equal(s.selectAllState(), 'none');
});

test('select all takes what is on screen, and leaves the rest of the vault alone', () => {
  // The listing menu offers it against a filtered view, so "all" has to mean
  // the rows the user can see - and it must not drop a selection made in
  // another view on the way.
  const s = store();
  s.setData({
    entries: [
      { uuid: 'e-1', name: 'GitHub', folder: null, tags: [], deleted_at: null },
      { uuid: 'e-2', name: 'Gitlab', folder: null, tags: [], deleted_at: null },
      { uuid: 'e-3', name: 'Bank', folder: null, tags: [], deleted_at: null },
    ],
  });
  s.selected = ['e-3'];
  s.search = 'git';
  s.selectAll();
  assert.deepStrictEqual(Array.from(s.selected).sort(), ['e-1', 'e-2', 'e-3']);
});
