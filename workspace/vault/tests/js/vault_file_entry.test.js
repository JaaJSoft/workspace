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
