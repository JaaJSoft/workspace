// Destroying entries for good is one request, not one per row. The loop it
// replaces could half-happen - half the rows gone, half in place, and nothing
// true left to tell the user - so the number of requests is asserted here
// rather than described.
const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

const VAULT_UUID = '11111111-1111-7111-8111-111111111111';

// The store's shape, not the server's: the listing decrypts rows before they
// reach the component, and `trashed` is what it writes deleted_at as.
function trashedRow(index) {
  return {
    uuid: `e-${index}`,
    folder: null,
    name: `Entry ${index}`,
    username: '',
    tags: [],
    favorite: false,
    trashed: true,
  };
}

function browser(options = {}) {
  const api = {
    purgeEntry: () => Promise.resolve(null),
    purgeEntries: () => Promise.resolve({ destroyed: [] }),
    purgeVaultTrash: () => Promise.resolve({ destroyed: [] }),
    fetchEntryActions: async () => ({}),
  };
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
      vaultApi: api,
      vaultSession: { isUnlocked: () => true },
      vaultClipboard: { cancel() {} },
      vaultCrypto: {},
    },
  );

  const count = options.count === undefined ? 3 : options.count;
  const component = ctx.vaultBrowser();
  component.openVault = { uuid: VAULT_UUID, name: 'Personal' };
  component.view = 'trash';
  component.entries = Array.from({ length: count }, (_, i) => trashedRow(i));
  component.selected = component.entries.map((entry) => entry.uuid);
  component.entryActions = Object.fromEntries(
    component.entries.map((entry) => [
      entry.uuid,
      [{ id: 'delete_forever', bulk: true }],
    ]),
  );
  // The listing reload is another module's job and would need the whole API
  // stubbed; what is under test here is the request that precedes it.
  let reloads = 0;
  component.load = async () => {
    reloads += 1;
  };
  return { component, api, reloads: () => reloads };
}

test('destroying a selection sends one request, not one per row', async () => {
  const { component, api } = browser();
  const calls = [];
  api.purgeEntries = (uuids) => {
    calls.push(uuids.slice());
    return Promise.resolve({ destroyed: uuids });
  };
  api.purgeEntry = () => {
    throw new Error('the single-entry endpoint must not be looped over');
  };

  await component.applyTo('delete_forever', component.selectedEntries());

  assert.equal(calls.length, 1);
  assert.deepEqual(Array.from(calls[0]), ['e-0', 'e-1', 'e-2']);
});

test('destroying one row still goes through the single-entry endpoint', async () => {
  // One row cannot half-happen, so it keeps the simpler answer - and that
  // endpoint keeps a caller rather than becoming dead code.
  const { component, api } = browser();
  let singles = 0;
  api.purgeEntry = () => {
    singles += 1;
    return Promise.resolve(null);
  };
  api.purgeEntries = () => {
    throw new Error('one row does not need the batch endpoint');
  };

  await component.applyTo('delete_forever', [component.selectedEntries()[0]]);

  assert.equal(singles, 1);
});

test('a refused batch reloads the listing and says so', async () => {
  // The reload comes first and the message after it: reloading is what makes
  // the listing describe what actually happened.
  const { component, api, reloads } = browser();
  api.purgeEntries = () => Promise.reject(new Error('409'));

  await component.applyTo('delete_forever', component.selectedEntries());

  assert.equal(reloads(), 1);
  assert.match(component.error, /could not be applied/);
});

test('the other bulk verbs still go row by row', async () => {
  // Only delete_forever has a batch endpoint behind it. A change that routed
  // every verb through one would be posting favourite re-signatures to a URL
  // that destroys things.
  const { component, api } = browser();
  const trashed = [];
  api.trashEntry = (uuid) => {
    trashed.push(uuid);
    return Promise.resolve(null);
  };

  await component.applyTo('trash', component.selectedEntries());

  assert.deepEqual(Array.from(trashed), ['e-0', 'e-1', 'e-2']);
});

test('the empty-trash button is offered only when every row offers delete_forever', () => {
  // The rule is the registry's, never the client's: a row the server would
  // refuse must not be swept up by a button that asked nobody.
  const { component } = browser();
  assert.equal(component.canEmptyTrash(), true);

  component.entryActions = Object.assign({}, component.entryActions, {
    'e-1': [],
  });

  assert.equal(component.canEmptyTrash(), false);
});

test('an empty trash offers nothing to empty', () => {
  const { component } = browser({ count: 0 });
  assert.equal(component.canEmptyTrash(), false);
});

test('the button is not offered outside the trash', () => {
  const { component } = browser();
  component.view = 'all';
  assert.equal(component.canEmptyTrash(), false);
});

test('a search narrowing the listing does not narrow what is destroyed', () => {
  // visibleEntries() is filtered by the search box; the trash is not. A
  // confirmation built from the filtered rows would understate what the
  // click destroys.
  const { component } = browser();
  component.search = 'a name no row carries';

  assert.equal(component.visibleEntries().length, 0);
  assert.equal(component.canEmptyTrash(), true);
  assert.equal(component.trashCount(), 3);
});

test('emptying the trash names the vault, never its rows', async () => {
  const { component, api, reloads } = browser();
  const calls = [];
  api.purgeVaultTrash = (vaultUuid) => {
    calls.push(vaultUuid);
    return Promise.resolve({ destroyed: [] });
  };
  api.purgeEntries = () => {
    throw new Error('emptying must not slice the trash into batches');
  };

  await component.emptyTrash();

  assert.deepEqual(Array.from(calls), [VAULT_UUID]);
  assert.equal(reloads(), 1);
});

test('a refused empty reloads the listing and says so', async () => {
  const { component, api, reloads } = browser();
  api.purgeVaultTrash = () => Promise.reject(new Error('409'));

  await component.emptyTrash();

  assert.equal(reloads(), 1);
  assert.match(component.error, /could not be emptied/);
});

test('emptying does nothing when the registry did not offer it', async () => {
  const { component, api } = browser();
  component.entryActions = {};
  api.purgeVaultTrash = () => {
    throw new Error('the request must not leave without the registry saying yes');
  };

  await component.emptyTrash();
});
