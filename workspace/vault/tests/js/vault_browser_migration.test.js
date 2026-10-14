// The migration runs in the background after an unlock: its result is only
// applied by the session that started it.
const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

const VAULT_UUID = '11111111-1111-7111-8111-111111111111';

function browser(run) {
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
      vaultApi: {},
      vaultSession,
      vaultClipboard: { cancel() {} },
      vaultCrypto: {},
      vaultMigration,
    },
  );
  const component = ctx.vaultBrowser();
  component.load = async () => { component.loads = (component.loads || 0) + 1; };
  return component;
}

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
