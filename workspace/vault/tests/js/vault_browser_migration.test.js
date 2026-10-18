// The migration runs in the background after an unlock: its result is only
// applied by the session that started it.
const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

const VAULT_UUID = '11111111-1111-7111-8111-111111111111';

function harness(run) {
  const vaultSession = { isUnlocked: () => true };
  const vaultMigration = { run };
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
      history: { replaceState() {} },
      setInterval: () => 1,
      clearInterval() {},
      TAG_CHIP_COLORS: [{ name: 'None', value: '' }],
      vaultApi: {
        updateEntry: async () => ({}),
        createEntry: async () => ({}),
        createFolder: async () => ({}),
      },
      vaultSession,
      vaultClipboard: { cancel() {} },
      vaultCrypto: {},
      vaultMigration,
    },
  );
  const component = ctx.vaultBrowser();
  component.load = async () => { component.loads = (component.loads || 0) + 1; };
  return { component, ctx };
}

function browser(run) {
  return harness(run).component;
}

const settle = () => new Promise((r) => setTimeout(r, 0));

test('a result from before a lock is dropped, even after an unlock', async () => {
  let resolve;
  const pending = new Promise((r) => { resolve = r; });
  const component = browser(() => pending);
  component.startMigration();
  component.onLocked();
  resolve({ outcome: 'failed', wrote: true, warn: true });
  await pending;
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(component.migrationWarning, false);
  assert.equal(component.loads || 0, 0);
});

test('a result of the current session is applied', async () => {
  const component = browser(async () => ({ outcome: 'clean', wrote: true, warn: true }));
  component.startMigration();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(component.migrationWarning, true);
  assert.equal(component.loads, 1);
});

test('a busy answer changes nothing', async () => {
  const component = browser(async () => ({ outcome: 'busy', wrote: false, warn: false }));
  component.migrationWarning = true;
  component.startMigration();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(component.migrationWarning, true);
});

// --- a reload that would take back a revealed secret -------------------------

test('a pass that wrote while a secret is revealed holds its reload', async () => {
  const component = browser(async () => ({ outcome: 'clean', wrote: true, warn: false }));
  component.revealed = { 'e-1|password': 'hunter2' };
  component.startMigration();
  await settle();
  assert.equal(component.loads || 0, 0);
  assert.equal(component.reloadDeferred, true);
  assert.equal(component.revealed['e-1|password'], 'hunter2');
});

test('a pass that wrote while a reveal is still decrypting holds its reload', async () => {
  const component = browser(async () => ({ outcome: 'clean', wrote: true, warn: false }));
  component.revealing = { 'e-1|password': true };
  component.startMigration();
  await settle();
  assert.equal(component.loads || 0, 0);
  assert.equal(component.reloadDeferred, true);
});

test('closing the panel runs the held reload, once', async () => {
  const component = browser(async () => ({ outcome: 'clean', wrote: true, warn: false }));
  component.revealed = { 'e-1|password': 'hunter2' };
  component.startMigration();
  await settle();
  component.resetPanel();
  await settle();
  assert.equal(component.loads, 1);
  assert.equal(component.reloadDeferred, false);
  component.resetPanel();
  await settle();
  assert.equal(component.loads, 1);
});

test('opening another entry runs the held reload', async () => {
  const component = browser(async () => ({ outcome: 'clean', wrote: true, warn: false }));
  component.revealed = { 'e-1|password': 'hunter2' };
  component.startMigration();
  await settle();
  assert.equal(component.loads || 0, 0);
  component.openEntryFromRow({ uuid: 'e-2', fieldIds: [] });
  await settle();
  assert.equal(component.loads, 1);
});

test('a lock drops the held reload without loading', async () => {
  const component = browser(async () => ({ outcome: 'clean', wrote: true, warn: false }));
  component.revealed = { 'e-1|password': 'hunter2' };
  component.startMigration();
  await settle();
  component.onLocked();
  await settle();
  assert.equal(component.reloadDeferred, false);
  assert.equal(component.loads || 0, 0);
});

// Every write that re-signs a stored row reloads first, so it signs what the
// server holds rather than the rows the migration has since rewritten.
function heldReload() {
  const { component, ctx } = harness(async () => ({ outcome: 'clean', wrote: false, warn: false }));
  const order = [];
  component.reloadDeferred = true;
  component.load = async () => { order.push('load'); };
  component.openVault = { uuid: VAULT_UUID };
  component.entryRows = [{ uuid: 'e-1', metadata_sig: 'sig' }];
  component.confirm = async () => true;
  const record = async () => { order.push('build'); return {}; };
  ctx.buildEntryResignRequest = record;
  ctx.buildEntryWriteRequest = record;
  ctx.buildFolderWriteRequest = record;
  ctx.vaultResign.deleteTagSafely = record;
  ctx.vaultResign.deleteFolderSafely = record;
  return { component, order };
}

test('a favourite toggle under a held reload reloads before it signs', async () => {
  const { component, order } = heldReload();
  await component.setFavorite('e-1', true);
  assert.deepStrictEqual(order.slice(0, 2), ['load', 'build']);
});

test('an entry save under a held reload reloads before it signs', async () => {
  const { component, order } = heldReload();
  component.draft = { uuid: 'e-1', name: 'GitHub', values: {}, carriedFields: {}, totpInput: null, isNew: true };
  await component.saveEntry();
  assert.deepStrictEqual(order.slice(0, 2), ['load', 'build']);
});

test('a folder save under a held reload reloads before it signs', async () => {
  const { component, order } = heldReload();
  component.folderDraft = { uuid: 'f-1', name: 'Bills', parent: null, position: 0 };
  await component.saveFolder();
  assert.deepStrictEqual(order.slice(0, 2), ['load', 'build']);
});

test('a tag deletion under a held reload reloads before it re-signs', async () => {
  const { component, order } = heldReload();
  await component.deleteTag({ uuid: 't-1', name: 'Work' });
  assert.deepStrictEqual(order.slice(0, 2), ['load', 'build']);
});

test('a folder deletion under a held reload reloads before it re-signs', async () => {
  const { component, order } = heldReload();
  await component.deleteFolder({ uuid: 'f-1', name: 'Bills' });
  assert.deepStrictEqual(order.slice(0, 2), ['load', 'build']);
});

// --- an edit opened before the pass rewrote its row --------------------------

const LOGIN = {
  id: 'login', label: 'Login', icon: 'key-round',
  fields: [
    { field_id: 'username', kind: 'text' },
    { field_id: 'password', kind: 'secret' },
    { field_id: 'totp', kind: 'totp' },
  ],
};

function storedEntry(suffix) {
  return {
    uuid: 'e-1',
    encrypted_notes: 'notes-' + suffix,
    entry_fields: [
      { field_id: 'password', encrypted_value: 'password-' + suffix },
      { field_id: 'totp', encrypted_value: 'totp-' + suffix },
    ],
    key_version: 1,
    entry_version: 1,
    metadata_sig: 'sig-' + suffix,
  };
}

// The dialog opened the row the pass is about to rewrite; the held reload
// brings the rewritten one in just before the save.
function editUnderHeldReload(rewritten) {
  const { component, ctx } = harness(async () => ({ outcome: 'clean', wrote: false, warn: false }));
  const built = [];
  component.entryTypes = [LOGIN];
  component.openVault = { uuid: VAULT_UUID };
  component.entryRows = [storedEntry('before')];
  component.draft = Object.assign({
    uuid: 'e-1', type: 'login', folder: null, tags: [], favorite: false, name: 'GitHub',
    notes: '', values: { password: 'hunter2' }, hasTotp: true, totpInput: null,
    totpRemoved: false, isNew: false,
  }, component.storedEntryParts(component.entryRows[0], 'login'));
  component.reloadDeferred = true;
  component.load = async () => { component.entryRows = rewritten ? [rewritten] : []; };
  ctx.buildEntryWriteRequest = async (session, vault, draft) => { built.push(draft); return {}; };
  return { component, built };
}

test('an edit saved after the pass carries the ciphertexts it left, not the ones it replaced', async () => {
  const { component, built } = editUnderHeldReload(storedEntry('after'));
  await component.saveEntry();
  assert.equal(built.length, 1);
  assert.deepStrictEqual({ ...built[0].carriedFields }, { totp: 'totp-after' });
  assert.equal(built[0].encryptedNotes, 'notes-after');
  assert.equal(built[0].storedSig, 'sig-after');
  assert.equal(built[0].values.password, 'hunter2', 'the typed value was replaced');
});

test('an edit whose row the reload no longer vouches for writes nothing and says why', async () => {
  const { component, built } = editUnderHeldReload(null);
  await component.saveEntry();
  assert.equal(built.length, 0);
  assert.equal(component.error, 'This entry changed since it was opened. Close it and open it again.');
});

test('a folder rename saved after the pass checks the signature it left', async () => {
  const { component, ctx } = harness(async () => ({ outcome: 'clean', wrote: false, warn: false }));
  const checked = [];
  component.openVault = { uuid: VAULT_UUID };
  component.folders = [{ uuid: 'f-1', parent: null, position: 0, name: 'Bills', metadata_sig: 'sig-before' }];
  component.renameFolder(component.folders[0]);
  component.reloadDeferred = true;
  component.load = async () => {
    component.folders = [{ uuid: 'f-1', parent: null, position: 0, name: 'Bills', metadata_sig: 'sig-after' }];
  };
  ctx.vaultResign.assertMayResign = (row) => { checked.push(row.metadata_sig); };
  ctx.buildFolderWriteRequest = async () => ({});
  await component.saveFolder();
  assert.deepStrictEqual(checked, ['sig-after']);
});
