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
