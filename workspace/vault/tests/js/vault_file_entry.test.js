// Moving and retagging entries is a re-signature of the stored rows: a change
// of folder or tags is inside the signed payload, so every row the batch
// touches is signed again and written back. The batch either refuses before
// its first request or stops at its first failure - never half-signed and
// silent.
const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

const VAULT_UUID = '11111111-1111-7111-8111-111111111111';
const SAVINGS = 'f-savings';
const WORK = 'f-work';
const TAG_A = 't-a';
const TAG_B = 't-b';
const TAG_C = 't-c';

// 'AQ' decodes to a signature prefix this build may re-sign; 'CQ' to one
// marked broken.
const RESIGNABLE = 'AQ';
const BROKEN = 'CQ';

function browser(rows, options = {}) {
  const ctxState = { locked: false };
  const ctx = loadScripts(
    [
      'workspace/vault/ui/static/vault/ui/js/vault_format.js',
      'workspace/vault/ui/static/vault/ui/js/vault_menu.js',
      'workspace/vault/ui/static/vault/ui/js/vault_tiles.js',
      'workspace/vault/ui/static/vault/ui/js/vault_prefs.js',
      'workspace/vault/ui/static/vault/ui/js/vault_view_prefs.js',
      'workspace/vault/ui/static/vault/ui/js/vault_unlock.js',
      'workspace/vault/ui/static/vault/ui/js/vault_store.js',
      'workspace/vault/ui/static/vault/ui/js/vault_reader.js',
      'workspace/vault/ui/static/vault/ui/js/entry_write.js',
      'workspace/vault/ui/static/vault/ui/js/folder_write.js',
      'workspace/vault/ui/static/vault/ui/js/tag_write.js',
      'workspace/vault/ui/static/vault/ui/js/clipboard.js',
      'workspace/vault/ui/static/vault/ui/js/vault_resign.js',
      'workspace/vault/ui/static/vault/ui/js/vault_switcher.js',
      'workspace/vault/ui/static/vault/ui/js/vault_generator.js',
      'workspace/vault/ui/static/vault/ui/js/vault_export.js',
      'workspace/vault/ui/static/vault/ui/js/vault_password.js',
      'workspace/vault/ui/static/vault/ui/js/vault_file_entry.js',
      'workspace/vault/ui/static/vault/ui/js/vault_browser.js',
    ],
    {
      TextEncoder: globalThis.TextEncoder,
      TextDecoder: globalThis.TextDecoder,
      URLSearchParams: globalThis.URLSearchParams,
      document: {
        getElementById: (id) =>
          id === 'vault-uuid' ? { textContent: JSON.stringify(VAULT_UUID) } : null,
        addEventListener() {},
      },
      location: { search: '' },
      localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
      addEventListener() {},
      CustomEvent: class { constructor(name) { this.type = name; } },
      dispatchEvent() {},
      navHistory: { state: () => ({}), push() {}, replace() {} },
      setInterval: () => 1,
      clearInterval() {},
      TAG_CHIP_COLORS: [{ name: 'None', value: '' }],
      vaultApi: {},
      vaultSession: { isUnlocked: () => !ctxState.locked },
      vaultClipboard: { cancel() {} },
      vaultCrypto: {
        fromBase64Url: (value) =>
          value === RESIGNABLE ? Uint8Array.of(1) : value === BROKEN ? Uint8Array.of(9) : value,
        mayResign: (id) => id !== 9,
      },
    },
  );

  const signed = [];
  const updates = [];
  let opened = 0;
  let loads = 0;
  ctx.buildEntryResignRequest = async (session, vault, row, changes) => {
    // The real builder refuses a broken signature too; asking here keeps the
    // stub honest about what it would have done.
    ctx.vaultResign.assertMayResign(row);
    signed.push(row.uuid);
    return { uuid: row.uuid, vault: vault.uuid, ...changes };
  };
  ctx.vaultApi.updateEntry = async (uuid, body) => {
    if (options.lockOn && options.lockOn === uuid) {
      // What a real lock does: the session goes, the page is wiped, and the
      // request in flight rejects as locked.
      ctxState.locked = true;
      component.onLocked();
      const err = new Error('locked');
      err.reason = 'locked';
      throw err;
    }
    if (options.failOn && options.failOn === uuid) throw new Error('500');
    updates.push({ uuid, body });
    return body;
  };
  ctx.vaultReader.openField = async () => {
    opened += 1;
    return 'secret';
  };

  const component = ctx.vaultBrowser();
  component.openVault = { uuid: VAULT_UUID, name: 'Personal' };
  component.folders = [
    { uuid: SAVINGS, name: 'Savings', parent: null },
    { uuid: WORK, name: 'Work', parent: null },
  ];
  component.tags = [
    { uuid: TAG_A, name: 'Bank', color: '' },
    { uuid: TAG_B, name: 'Home', color: '' },
    { uuid: TAG_C, name: 'Travel', color: '' },
  ];
  component.entryRows = rows.map((row) => ({
    folder: null,
    tags: [],
    metadata_sig: RESIGNABLE,
    ...row,
  }));
  component.entries = component.entryRows.map((row) => ({
    uuid: row.uuid,
    folder: row.folder,
    tags: [...row.tags],
  }));
  component.load = async () => {
    loads += 1;
  };

  return {
    component,
    ctx,
    signed,
    updates,
    opened: () => opened,
    loads: () => loads,
  };
}

test('a move re-signs only the rows that are not already there', async () => {
  const { component, updates } = browser([
    { uuid: 'e-1', folder: WORK },
    { uuid: 'e-2', folder: SAVINGS },
    { uuid: 'e-3', folder: null },
  ]);

  await component.moveEntries(['e-1', 'e-2', 'e-3'], SAVINGS);

  assert.deepStrictEqual(updates.map((u) => u.uuid), ['e-1', 'e-3']);
  assert.ok(updates.every((u) => u.body.folder === SAVINGS));
  assert.equal(component.fileNotice, 'Moved 2 entries to Savings.');
  assert.equal(component.error, '');
});

test('a move to no folder names the vault, and one entry is singular', async () => {
  const { component, updates } = browser([{ uuid: 'e-1', folder: WORK }]);

  await component.moveEntries(['e-1'], null);

  assert.equal(updates.length, 1);
  assert.equal(updates[0].body.folder, null);
  assert.equal(component.fileNotice, 'Moved 1 entry to Personal.');
});

test('a row that may not be re-signed stops the batch before any request', async () => {
  const { component, updates } = browser([
    { uuid: 'e-1', folder: null },
    { uuid: 'e-2', folder: null, metadata_sig: BROKEN },
    { uuid: 'e-3', folder: null },
  ]);

  await component.moveEntries(['e-1', 'e-2', 'e-3'], SAVINGS);

  assert.equal(updates.length, 0, 'not even the row before the refused one');
  assert.equal(component.error, 'An entry could not be verified. Nothing was changed.');
  assert.equal(component.fileNotice, '');
});

test('a row already in place is never pre-checked', async () => {
  // The refused row needs no change, so it is not part of the batch at all:
  // refusing the batch over it would block a move that never touches it.
  const { component, updates } = browser([
    { uuid: 'e-1', folder: null },
    { uuid: 'e-2', folder: SAVINGS, metadata_sig: BROKEN },
    { uuid: 'e-3', folder: WORK },
  ]);

  await component.moveEntries(['e-1', 'e-2', 'e-3'], SAVINGS);

  assert.deepStrictEqual(updates.map((u) => u.uuid), ['e-1', 'e-3']);
  assert.equal(component.error, '');
  assert.equal(component.fileNotice, 'Moved 2 entries to Savings.');
});

test('a failed write stops the batch and reloads', async () => {
  const { component, updates, loads } = browser(
    [
      { uuid: 'e-1', folder: null },
      { uuid: 'e-2', folder: null },
      { uuid: 'e-3', folder: null },
    ],
    { failOn: 'e-2' },
  );

  const written = await component.resignEntries({
    'e-1': { folder: SAVINGS },
    'e-2': { folder: SAVINGS },
    'e-3': { folder: SAVINGS },
  });

  assert.equal(written, 1);
  assert.deepStrictEqual(updates.map((u) => u.uuid), ['e-1']);
  assert.equal(loads(), 1);
  assert.equal(
    component.error,
    'Some entries could not be changed. The list above is up to date.',
  );
  assert.equal(component.fileNotice, '');
});

test('a lock mid-batch leaves no notice and no reload behind', async () => {
  const { component, updates, loads } = browser(
    [
      { uuid: 'e-1', folder: null },
      { uuid: 'e-2', folder: null },
      { uuid: 'e-3', folder: null },
    ],
    { lockOn: 'e-2' },
  );

  await component.moveEntries(['e-1', 'e-2', 'e-3'], SAVINGS);

  assert.equal(updates.length, 1);
  assert.equal(component.fileNotice, '');
  assert.equal(loads(), 0);
});

test('the next batch clears the previous notice', async () => {
  const { component } = browser([{ uuid: 'e-1', folder: null }], { failOn: 'e-1' });
  component.fileNotice = 'Moved 2 entries to Savings.';

  await component.resignEntries({ 'e-1': { folder: WORK } });

  assert.equal(component.fileNotice, '');
});

test('a move never opens a field', async () => {
  const { component, opened, updates } = browser([
    { uuid: 'e-1', folder: null },
    { uuid: 'e-2', folder: WORK },
  ]);

  await component.moveEntries(['e-1', 'e-2'], SAVINGS);

  assert.equal(updates.length, 2);
  assert.equal(opened(), 0);
});

test('tag states read all, some and none across the selection', () => {
  const { component } = browser([
    { uuid: 'e-1', tags: [TAG_A, TAG_B] },
    { uuid: 'e-2', tags: [TAG_A] },
  ]);

  assert.deepStrictEqual(
    { ...component.tagStates(['e-1', 'e-2']) },
    { [TAG_A]: 'all', [TAG_B]: 'some', [TAG_C]: 'none' },
  );
});

test("a dash leaves each entry's tag as it was", () => {
  const { component } = browser([
    { uuid: 'e-1', tags: [TAG_A, TAG_B] },
    { uuid: 'e-2', tags: [TAG_A] },
  ]);

  const changes = component.tagChangesFor(['e-1', 'e-2'], {
    [TAG_A]: 'all',
    [TAG_B]: 'some',
    [TAG_C]: 'none',
  });

  assert.deepStrictEqual(Object.keys(changes), []);
});

test('ticking adds only where missing', () => {
  const { component } = browser([
    { uuid: 'e-1', tags: [TAG_A, TAG_B] },
    { uuid: 'e-2', tags: [TAG_A] },
  ]);

  const changes = component.tagChangesFor(['e-1', 'e-2'], {
    [TAG_A]: 'all',
    [TAG_B]: 'all',
    [TAG_C]: 'none',
  });

  assert.deepStrictEqual(Object.keys(changes), ['e-2']);
  assert.deepStrictEqual(Array.from(changes['e-2'].tags), [TAG_A, TAG_B]);
});

test('clearing removes only where present', () => {
  const { component } = browser([
    { uuid: 'e-1', tags: [TAG_A, TAG_B] },
    { uuid: 'e-2', tags: [TAG_A] },
    { uuid: 'e-3', tags: [TAG_C] },
  ]);

  const changes = component.tagChangesFor(['e-1', 'e-2', 'e-3'], {
    [TAG_A]: 'none',
    [TAG_B]: 'some',
    [TAG_C]: 'some',
  });

  assert.deepStrictEqual(Object.keys(changes), ['e-1', 'e-2']);
  assert.deepStrictEqual(Array.from(changes['e-1'].tags), [TAG_B]);
  assert.deepStrictEqual(Array.from(changes['e-2'].tags), []);
});

test('an entry whose tags do not change is not written', async () => {
  const { component, updates } = browser([
    { uuid: 'e-1', tags: [TAG_A, TAG_B] },
    { uuid: 'e-2', tags: [TAG_C] },
  ]);

  // The same set in another order is the same tags: the signature would
  // change and nothing the user sees would.
  const written = await component.resignEntries({
    'e-1': { tags: [TAG_B, TAG_A] },
    'e-2': { tags: [TAG_C, TAG_A] },
  });

  assert.equal(written, 1);
  assert.deepStrictEqual(updates.map((u) => u.uuid), ['e-2']);
});

test('the registry ids move and set_tags open their dialogs', async () => {
  const { component } = browser([
    { uuid: 'e-1', folder: WORK, tags: [TAG_A] },
  ]);
  component.entryActions = { 'e-1': [{ id: 'move' }, { id: 'set_tags' }] };
  const entry = component.entries[0];

  assert.equal(component.moveDialog, null);
  assert.equal(component.tagsDialog, null);

  await component.runAction({ id: 'move' }, entry);
  assert.deepStrictEqual(Array.from(component.moveDialog.uuids), ['e-1']);
  assert.equal(component.moveDialog.target, undefined);

  await component.runAction({ id: 'set_tags' }, entry);
  assert.deepStrictEqual(Array.from(component.tagsDialog.uuids), ['e-1']);
  assert.deepStrictEqual(
    { ...component.tagsDialog.start },
    { [TAG_A]: 'all', [TAG_B]: 'none', [TAG_C]: 'none' },
  );
  assert.deepStrictEqual({ ...component.tagsDialog.states }, { ...component.tagsDialog.start });
  assert.notStrictEqual(component.tagsDialog.states, component.tagsDialog.start);
});

test('the selection bar opens the same dialogs for every selected row', async () => {
  const { component } = browser([
    { uuid: 'e-1', folder: WORK },
    { uuid: 'e-2', folder: null },
  ]);
  const offer = [{ id: 'move', bulk: true }, { id: 'set_tags', bulk: true }];
  component.entryActions = { 'e-1': offer, 'e-2': offer };
  component.selected = ['e-1', 'e-2'];

  await component.runBulkAction({ id: 'move' });
  assert.deepStrictEqual(Array.from(component.moveDialog.uuids), ['e-1', 'e-2']);

  await component.runBulkAction({ id: 'set_tags' });
  assert.deepStrictEqual(Array.from(component.tagsDialog.uuids), ['e-1', 'e-2']);
});

test('the folder tree is depth-first with depths', () => {
  const { component } = browser([]);
  component.folders = [
    { uuid: 'f-b', name: 'Beta', parent: null },
    { uuid: 'f-a', name: 'Alpha', parent: null },
    { uuid: 'f-a2', name: 'Inner', parent: 'f-a' },
    { uuid: 'f-a21', name: 'Deep', parent: 'f-a2' },
    { uuid: 'f-b1', name: 'Child', parent: 'f-b' },
  ];

  const tree = Array.from(component.folderTree(), (f) => [f.uuid, f.depth]);

  assert.deepStrictEqual(tree, [
    ['f-b', 0], ['f-b1', 1], ['f-a', 0], ['f-a2', 1], ['f-a21', 2],
  ]);
});

test('the common folder is undefined for a mixed selection', () => {
  const { component } = browser([
    { uuid: 'e-1', folder: WORK },
    { uuid: 'e-2', folder: WORK },
    { uuid: 'e-3', folder: null },
  ]);

  assert.equal(component.commonFolder(['e-1', 'e-2']), WORK);
  assert.equal(component.commonFolder(['e-3']), null);
  assert.equal(component.commonFolder(['e-1', 'e-3']), undefined);
});

test('Move stays disabled on the current place', () => {
  const { component } = browser([
    { uuid: 'e-1', folder: WORK },
    { uuid: 'e-2', folder: WORK },
  ]);
  component.openMoveDialog([{ uuid: 'e-1' }, { uuid: 'e-2' }]);

  assert.equal(component.canConfirmMove(), false, 'nothing chosen yet');
  component.moveDialog.target = WORK;
  assert.equal(component.canConfirmMove(), false, 'the place they already sit in');
  component.moveDialog.target = SAVINGS;
  assert.equal(component.canConfirmMove(), true);
  component.moveDialog.target = null;
  assert.equal(component.canConfirmMove(), true, 'the vault root is a place');
  component.busy = true;
  assert.equal(component.canConfirmMove(), false, 'a batch is already running');
});

test('a mixed selection can move anywhere', () => {
  const { component } = browser([
    { uuid: 'e-1', folder: WORK },
    { uuid: 'e-2', folder: null },
  ]);
  component.openMoveDialog([{ uuid: 'e-1' }, { uuid: 'e-2' }]);
  component.moveDialog.target = WORK;

  assert.equal(component.canConfirmMove(), true);
});

test('confirming a move writes the chosen place and closes the dialog', async () => {
  const { component, updates } = browser([{ uuid: 'e-1', folder: WORK }]);
  component.entryActions = { 'e-1': [{ id: 'move' }] };
  component.openMoveDialog([{ uuid: 'e-1' }]);
  component.moveDialog.target = SAVINGS;

  await component.confirmMove();

  assert.deepStrictEqual(updates.map((u) => u.body.folder), [SAVINGS]);
  assert.equal(component.moveDialog, null);
  assert.equal(component.fileNotice, 'Moved 1 entry to Savings.');
});

test('a move whose offer was withdrawn while the dialog was open writes nothing', async () => {
  const { component, updates } = browser([
    { uuid: 'e-1', folder: WORK },
    { uuid: 'e-2', folder: WORK },
  ]);
  component.entryActions = { 'e-1': [{ id: 'move' }], 'e-2': [{ id: 'move' }] };
  component.openMoveDialog([{ uuid: 'e-1' }, { uuid: 'e-2' }]);
  component.moveDialog.target = SAVINGS;
  component.entryActions = { 'e-1': [{ id: 'move' }], 'e-2': [] };

  await component.confirmMove();

  assert.equal(updates.length, 0);
  assert.equal(component.moveDialog, null);
});

test('a move whose entry vanished while the dialog was open writes nothing', async () => {
  const { component, updates } = browser([{ uuid: 'e-1', folder: WORK }]);
  component.entryActions = { 'e-1': [{ id: 'move' }] };
  component.openMoveDialog([{ uuid: 'e-1' }]);
  component.moveDialog.target = SAVINGS;
  component.entries = [];

  await component.confirmMove();

  assert.equal(updates.length, 0);
  assert.equal(component.moveDialog, null);
});

test('cycling skips the dash for a tag that did not start mixed', () => {
  const { component } = browser([
    { uuid: 'e-1', tags: [TAG_A, TAG_B] },
    { uuid: 'e-2', tags: [TAG_A] },
  ]);
  component.openTagsDialog([{ uuid: 'e-1' }, { uuid: 'e-2' }]);
  const states = component.tagsDialog.states;

  const walk = (tag, times) => {
    const seen = [];
    for (let i = 0; i < times; i += 1) {
      component.cycleTag(tag);
      seen.push(states[tag]);
    }
    return seen;
  };

  assert.deepStrictEqual(walk(TAG_A, 3), ['none', 'all', 'none'], 'carried by all');
  assert.deepStrictEqual(walk(TAG_C, 3), ['all', 'none', 'all'], 'carried by none');
  assert.deepStrictEqual(walk(TAG_B, 3), ['all', 'none', 'some'], 'mixed at the start');
});

test('the summary counts only the entries that change', () => {
  const { component } = browser([
    { uuid: 'e-1', tags: [TAG_A, TAG_B] },
    { uuid: 'e-2', tags: [TAG_A] },
    { uuid: 'e-3', tags: [] },
  ]);
  component.openTagsDialog([{ uuid: 'e-1' }, { uuid: 'e-2' }, { uuid: 'e-3' }]);
  component.tagsDialog.states[TAG_A] = 'none';
  component.tagsDialog.states[TAG_B] = 'all';
  component.tagsDialog.states[TAG_C] = 'all';

  assert.deepStrictEqual(Array.from(component.tagSummary()), [
    'Removes Bank (2 entries)',
    'Adds Home (2 entries)',
    'Adds Travel (3 entries)',
  ]);
});

test('the summary names a single entry without a count', () => {
  const { component } = browser([{ uuid: 'e-1', tags: [TAG_A] }]);
  component.openTagsDialog([{ uuid: 'e-1' }]);
  component.tagsDialog.states[TAG_A] = 'none';
  component.tagsDialog.states[TAG_B] = 'all';

  assert.deepStrictEqual(Array.from(component.tagSummary()), ['Removes Bank', 'Adds Home']);
});

test('the summary counts one entry as an entry', () => {
  const { component } = browser([
    { uuid: 'e-1', tags: [TAG_A] },
    { uuid: 'e-2', tags: [] },
  ]);
  component.openTagsDialog([{ uuid: 'e-1' }, { uuid: 'e-2' }]);
  component.tagsDialog.states[TAG_A] = 'all';

  assert.deepStrictEqual(Array.from(component.tagSummary()), ['Adds Bank (1 entry)']);
});

test('the number of entries carrying a tag is read from the selection', () => {
  const { component } = browser([
    { uuid: 'e-1', tags: [TAG_A] },
    { uuid: 'e-2', tags: [TAG_A] },
    { uuid: 'e-3', tags: [] },
  ]);
  component.openTagsDialog([{ uuid: 'e-1' }, { uuid: 'e-3' }]);

  assert.equal(component.dialogTagCount(TAG_A), 1);
});

test('Apply is disabled with nothing to change', () => {
  const { component } = browser([
    { uuid: 'e-1', tags: [TAG_A, TAG_B] },
    { uuid: 'e-2', tags: [TAG_A] },
  ]);
  assert.equal(component.canConfirmTags(), false, 'no dialog');
  component.openTagsDialog([{ uuid: 'e-1' }, { uuid: 'e-2' }]);
  assert.equal(component.canConfirmTags(), false, 'nothing clicked');

  component.cycleTag(TAG_C);
  assert.equal(component.canConfirmTags(), true);
  component.cycleTag(TAG_C);
  assert.equal(component.canConfirmTags(), false, 'back where it started');

  component.cycleTag(TAG_C);
  component.busy = true;
  assert.equal(component.canConfirmTags(), false, 'a batch is already running');
});

test('confirming tags writes only the entries that change and shows no notice', async () => {
  const { component, updates } = browser([
    { uuid: 'e-1', tags: [TAG_A, TAG_B] },
    { uuid: 'e-2', tags: [TAG_A] },
  ]);
  component.entryActions = { 'e-1': [{ id: 'set_tags' }], 'e-2': [{ id: 'set_tags' }] };
  component.openTagsDialog([{ uuid: 'e-1' }, { uuid: 'e-2' }]);
  component.cycleTag(TAG_C);

  await component.confirmTags();

  assert.deepStrictEqual(updates.map((u) => u.uuid), ['e-1', 'e-2']);
  assert.deepStrictEqual(Array.from(updates[0].body.tags), [TAG_A, TAG_B, TAG_C]);
  assert.deepStrictEqual(Array.from(updates[1].body.tags), [TAG_A, TAG_C]);
  assert.equal(component.tagsDialog, null);
  assert.equal(component.fileNotice, '');
});

test('tags whose offer was withdrawn while the dialog was open write nothing', async () => {
  const { component, updates } = browser([
    { uuid: 'e-1', tags: [] },
    { uuid: 'e-2', tags: [] },
  ]);
  component.entryActions = { 'e-1': [{ id: 'set_tags' }], 'e-2': [{ id: 'set_tags' }] };
  component.openTagsDialog([{ uuid: 'e-1' }, { uuid: 'e-2' }]);
  component.cycleTag(TAG_A);
  component.entryActions = { 'e-1': [{ id: 'set_tags' }], 'e-2': [] };

  await component.confirmTags();

  assert.equal(updates.length, 0);
  assert.equal(component.tagsDialog, null);
});

test('tags whose entry vanished while the dialog was open write nothing', async () => {
  const { component, updates } = browser([{ uuid: 'e-1', tags: [] }]);
  component.entryActions = { 'e-1': [{ id: 'set_tags' }] };
  component.openTagsDialog([{ uuid: 'e-1' }]);
  component.cycleTag(TAG_A);
  component.entries = [];

  await component.confirmTags();

  assert.equal(updates.length, 0);
  assert.equal(component.tagsDialog, null);
});

function dragEvent() {
  const data = {};
  return {
    data,
    prevented: false,
    preventDefault() { this.prevented = true; },
    dataTransfer: {
      effectAllowed: '',
      dropEffect: '',
      setData(type, value) { data[type] = value; },
    },
  };
}

test('a selected row drags the whole selection', () => {
  const { component } = browser([
    { uuid: 'e-1' }, { uuid: 'e-2' }, { uuid: 'e-3' },
  ]);
  component.selected = ['e-1', 'e-3'];
  const event = dragEvent();

  component.onEntryDragStart(event, component.entries[2]);

  assert.deepStrictEqual(Array.from(component.draggedUuids), ['e-1', 'e-3']);
  assert.equal(event.dataTransfer.effectAllowed, 'move');
  assert.equal(event.data['application/x-vault-entries'], 'e-1,e-3');
});

test('an unselected row drags alone', () => {
  const { component } = browser([{ uuid: 'e-1' }, { uuid: 'e-2' }]);
  component.selected = ['e-1'];
  const event = dragEvent();

  component.onEntryDragStart(event, component.entries[1]);

  assert.deepStrictEqual(Array.from(component.draggedUuids), ['e-2']);
  assert.equal(event.dataTransfer.effectAllowed, 'move');
});

test('rows are not draggable outside a folder view', () => {
  const { component } = browser([{ uuid: 'e-1' }]);
  assert.equal(component.canDragEntries(), true);

  component.view = 'favorites';
  assert.equal(component.canDragEntries(), false);
  component.view = 'trash';
  assert.equal(component.canDragEntries(), false);
  component.view = 'all';
  component.tagFilter = TAG_A;
  assert.equal(component.canDragEntries(), false);

  const event = dragEvent();
  component.onEntryDragStart(event, component.entries[0]);
  assert.equal(event.prevented, true);
  assert.deepStrictEqual(Array.from(component.draggedUuids), []);
});

test('the current folder refuses the drop, any other accepts it as a move', () => {
  const { component } = browser([{ uuid: 'e-1', folder: SAVINGS }]);
  component.folderUuid = SAVINGS;
  component.draggedUuids = ['e-1'];

  const here = dragEvent();
  component.onFolderDragOver(here, SAVINGS);
  assert.equal(here.prevented, false);
  assert.equal(component.dropTarget, undefined);

  const there = dragEvent();
  component.onFolderDragOver(there, WORK);
  assert.equal(there.prevented, true);
  assert.equal(there.dataTransfer.dropEffect, 'move');
  assert.equal(component.dropTarget, WORK);

  const root = dragEvent();
  component.onFolderDragOver(root, null);
  assert.equal(root.prevented, true);
  assert.equal(component.dropTarget, null);
});

test('the vault root refuses the drop while the root is shown', () => {
  const { component } = browser([{ uuid: 'e-1' }]);
  component.draggedUuids = ['e-1'];
  const event = dragEvent();

  component.onFolderDragOver(event, null);

  assert.equal(event.prevented, false);
});

test('a drop moves the dragged rows through the shared write path', async () => {
  const { component, updates } = browser([
    { uuid: 'e-1', folder: null }, { uuid: 'e-2', folder: null },
  ]);
  component.entryActions = { 'e-1': [{ id: 'move' }], 'e-2': [{ id: 'move' }] };
  component.draggedUuids = ['e-1', 'e-2'];

  await component.onFolderDrop(dragEvent(), SAVINGS);

  assert.deepStrictEqual(updates.map((u) => u.uuid), ['e-1', 'e-2']);
  assert.equal(component.fileNotice, 'Moved 2 entries to Savings.');
  assert.deepStrictEqual(Array.from(component.draggedUuids), []);
  assert.equal(component.dropTarget, undefined);
});

test('a drop writes nothing when the registry no longer offers a move for a row', async () => {
  const { component, updates } = browser([
    { uuid: 'e-1', folder: null }, { uuid: 'e-2', folder: null },
  ]);
  component.entryActions = { 'e-1': [{ id: 'move' }], 'e-2': [] };
  component.draggedUuids = ['e-1', 'e-2'];

  await component.onFolderDrop(dragEvent(), SAVINGS);

  assert.equal(updates.length, 0);
});

test('a drop while a write is running writes nothing', async () => {
  const { component, updates } = browser([{ uuid: 'e-1', folder: null }]);
  component.entryActions = { 'e-1': [{ id: 'move' }] };
  component.draggedUuids = ['e-1'];
  component.busy = true;

  await component.onFolderDrop(dragEvent(), SAVINGS);

  assert.equal(updates.length, 0);
});

test('apply gives back only the tags the user clicked, not ones a reload changed', async () => {
  const { component, updates } = browser([
    { uuid: 'e-1', tags: [TAG_A] },
    { uuid: 'e-2', tags: [TAG_A] },
  ]);
  component.entryActions = { 'e-1': [{ id: 'set_tags' }], 'e-2': [{ id: 'set_tags' }] };
  component.openTagsDialog([{ uuid: 'e-1' }, { uuid: 'e-2' }]);
  component.cycleTag(TAG_C);
  // A reload lands under the open dialog: e-2 no longer carries TAG_A.
  component.entryRows.find((row) => row.uuid === 'e-2').tags = [];

  await component.confirmTags();

  assert.deepStrictEqual(updates.map((u) => u.uuid), ['e-1', 'e-2']);
  assert.deepStrictEqual(Array.from(updates[0].body.tags), [TAG_A, TAG_C]);
  assert.deepStrictEqual(Array.from(updates[1].body.tags), [TAG_C]);
});

test('loading another vault drops the notice and any open dialog', async () => {
  const { component, ctx } = browser([{ uuid: 'e-1' }]);
  component.fileNotice = 'Moved 1 entry to Savings.';
  component.openMoveDialog([{ uuid: 'e-1' }]);
  component.openTagsDialog([{ uuid: 'e-1' }]);
  component.vaultUuid = 'other-vault';
  ctx.vaultApi.listVaults = async () => [{ uuid: 'other-vault' }];
  ctx.vaultReader.readVault = async (session, row) => ({ uuid: row.uuid, name: 'Other' });
  component.syncEntry = () => {};
  component.resetNavigation = () => {};

  await component.loadVault();

  assert.equal(component.fileNotice, '');
  assert.equal(component.moveDialog, null);
  assert.equal(component.tagsDialog, null);
});

test('a dialog reopened during the write is not closed by the first batch', async () => {
  const { component, ctx } = browser([{ uuid: 'e-1', folder: null }]);
  component.entryActions = { 'e-1': [{ id: 'move' }] };
  component.openMoveDialog([{ uuid: 'e-1' }]);
  component.moveDialog.target = SAVINGS;
  ctx.vaultApi.updateEntry = async (uuid, body) => {
    component.closeMoveDialog();
    component.openMoveDialog([{ uuid: 'e-1' }]);
    return body;
  };

  await component.confirmMove();

  assert.notEqual(component.moveDialog, null);
});

test('a tags dialog reopened during the write is not closed by the first batch', async () => {
  const { component, ctx } = browser([{ uuid: 'e-1', tags: [] }]);
  component.entryActions = { 'e-1': [{ id: 'set_tags' }] };
  component.openTagsDialog([{ uuid: 'e-1' }]);
  component.cycleTag(TAG_A);
  ctx.vaultApi.updateEntry = async (uuid, body) => {
    component.closeTagsDialog();
    component.openTagsDialog([{ uuid: 'e-1' }]);
    return body;
  };

  await component.confirmTags();

  assert.notEqual(component.tagsDialog, null);
});

test('a second batch started during the deferred reload writes nothing', async () => {
  const { component, updates } = browser([{ uuid: 'e-1', folder: WORK }]);
  component.entryActions = { 'e-1': [{ id: 'move' }] };
  let release;
  component.reloadDeferred = true;
  let pending = true;
  component.load = () => {
    if (!pending) return Promise.resolve();
    pending = false;
    return new Promise((resolve) => { release = resolve; });
  };

  const first = component.moveEntries(['e-1'], SAVINGS);
  assert.equal(component.busy, true);
  component.draggedUuids = ['e-1'];
  await component.onFolderDrop(dragEvent(), SAVINGS);
  const second = await component.moveEntries(['e-1'], SAVINGS);
  assert.equal(second, undefined);
  assert.equal(updates.length, 0);

  release();
  await first;
  assert.deepStrictEqual(updates.map((u) => u.uuid), ['e-1']);
  assert.equal(component.busy, false);
});

test('a batch signs with the vault the deferred reload brought back', async () => {
  // The deferred reload is what a migration pass asks for, and it can hand
  // back the vault under a new key generation: signing with the row read
  // before it would seal the batch under a key the vault has left.
  const { component, ctx } = browser([{ uuid: 'e-1', folder: WORK }]);
  const fresh = { uuid: VAULT_UUID, name: 'Personal', key_version: 2 };
  component.reloadDeferred = true;
  component.load = async () => { component.openVault = fresh; };
  const signedWith = [];
  const build = ctx.buildEntryResignRequest;
  ctx.buildEntryResignRequest = async (session, vault, row, changes) => {
    signedWith.push(vault);
    return build(session, vault, row, changes);
  };

  await component.moveEntries(['e-1'], SAVINGS);

  assert.equal(signedWith.length, 1);
  assert.equal(signedWith[0], fresh);
});

test('the batch releases busy when it has nothing to write', async () => {
  const { component } = browser([{ uuid: 'e-1', folder: WORK }]);

  await component.moveEntries(['e-1'], WORK);

  assert.equal(component.busy, false);
});

test('a drop the registry refuses says so', async () => {
  const { component, updates } = browser([{ uuid: 'e-1', folder: null }]);
  component.entryActions = { 'e-1': [] };
  component.draggedUuids = ['e-1'];

  await component.onFolderDrop(dragEvent(), SAVINGS);

  assert.equal(updates.length, 0);
  assert.equal(
    component.error,
    'An entry can no longer be changed this way. Nothing was changed.',
  );
});

test('a move the registry withdrew says so', async () => {
  const { component } = browser([{ uuid: 'e-1', folder: WORK }]);
  component.entryActions = { 'e-1': [{ id: 'move' }] };
  component.openMoveDialog([{ uuid: 'e-1' }]);
  component.moveDialog.target = SAVINGS;
  component.entryActions = { 'e-1': [] };

  await component.confirmMove();

  assert.equal(component.moveDialog, null);
  assert.equal(
    component.error,
    'An entry can no longer be changed this way. Nothing was changed.',
  );
});

test('tags the registry withdrew say so', async () => {
  const { component } = browser([{ uuid: 'e-1', tags: [] }]);
  component.entryActions = { 'e-1': [{ id: 'set_tags' }] };
  component.openTagsDialog([{ uuid: 'e-1' }]);
  component.tagsDialog.states[TAG_A] = 'all';
  component.entryActions = { 'e-1': [] };

  await component.confirmTags();

  assert.equal(component.tagsDialog, null);
  assert.equal(
    component.error,
    'An entry can no longer be changed this way. Nothing was changed.',
  );
});

test('navigating to another place drops the notice of the last move', async () => {
  const { component } = browser([{ uuid: 'e-1', folder: WORK }]);
  await component.moveEntries(['e-1'], SAVINGS);
  assert.equal(component.fileNotice, 'Moved 1 entry to Savings.');

  component.apply({ view: 'all', folder: WORK, tag: null });

  assert.equal(component.fileNotice, '');
});

test('the notice survives the reload of the batch that set it', async () => {
  const { component } = browser([{ uuid: 'e-1', folder: WORK }]);
  component.load = async () => {
    component.apply({ view: 'all', folder: component.folderUuid || null, tag: null });
  };
  await component.moveEntries(['e-1'], SAVINGS);
  assert.equal(component.fileNotice, 'Moved 1 entry to Savings.');
});
