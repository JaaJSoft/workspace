// Destroying entries permanently is one request, not one per row. The loop
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
  // The stored rows behind the opened ones. A row that fails to verify lives
  // only here: readAll drops it from `entries` and counts it as tampered.
  const unreadable = options.unreadable || 0;
  component.entryRows = Array.from(
    { length: count + unreadable },
    (_, i) => ({ uuid: `e-${i}`, deleted_at: '2026-09-20T00:00:00Z' }),
  );
  component.selected = component.entries.map((entry) => entry.uuid);
  // Keyed on the stored rows, the way loadEntryActions asks: the endpoint
  // answers for a row this device could not open, and the trash button is
  // the one control that acts on those.
  component.entryActions = Object.fromEntries(
    component.entryRows.map((row) => [
      row.uuid,
      [{ id: 'delete_forever', bulk: true }],
    ]),
  );
  // The listing reload is another module's job and would need the whole API
  // stubbed; what is under test here is the request that precedes it.
  let reloads = 0;
  component.load = async () => {
    reloads += 1;
  };
  return { component, api, ctx, reloads: () => reloads };
}

test('destroying a selection sends one request, not one per row', async () => {
  const { component, api } = browser();
  const calls = [];
  api.purgeEntries = (uuids) => {
    calls.push(uuids.slice());
    return Promise.resolve({ destroyed: uuids });
  };

  await component.applyTo('delete_forever', component.selectedEntries());

  assert.equal(calls.length, 1);
  assert.deepEqual(Array.from(calls[0]), ['e-0', 'e-1', 'e-2']);
});

test('destroying one row goes through the same endpoint as a hundred', async () => {
  // One way to destroy an entry rather than two that could answer
  // differently: there is no per-row purge endpoint behind this any more.
  const { component, api } = browser();
  const calls = [];
  api.purgeEntries = (uuids) => {
    calls.push(uuids.slice());
    return Promise.resolve({ destroyed: uuids });
  };

  await component.applyTo('delete_forever', [component.selectedEntries()[0]]);

  assert.deepEqual(Array.from(calls), [['e-0']]);
});

test('a refused batch reloads the listing and says so', async () => {
  // The reload comes first and the message after it: reloading is what makes
  // the listing describe what actually happened.
  const { component, api, reloads } = browser();
  api.purgeEntries = () => Promise.reject(new Error('409'));

  await component.applyTo('delete_forever', component.selectedEntries());

  assert.equal(reloads(), 1);
  assert.match(component.error, /Some entries could not be changed/);
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

test('a trash of rows this device cannot read can still be emptied', () => {
  // Those rows never reach `entries`, so they have no menu of their own
  // either: gating the button on the opened rows would leave a user looking
  // at a trash full of entries nothing on the page can remove.
  const { component } = browser({ count: 0, unreadable: 2 });

  assert.equal(component.trashCount(), 0);
  assert.equal(component.canEmptyTrash(), true);
});

test('the actions request covers the rows that did not open', async () => {
  // canEmptyTrash reads the registry's answer for every stored row, so the
  // question has to be asked about every stored row.
  const { component, api } = browser({ count: 2, unreadable: 1 });
  const asked = [];
  api.fetchEntryActions = async (uuids) => {
    asked.push(...uuids);
    return {};
  };

  await component.loadEntryActions();

  assert.deepEqual(asked.slice().sort(), ['e-0', 'e-1', 'e-2']);
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

test('a selection larger than the cap is destroyed in slices of it', () => {
  // Nothing caps a selection - Select all ticks every listed row - while the
  // endpoint refuses more than VAULT_PURGE_BATCH_SIZE. Before this slicing a
  // trash of 250 answered 400 and destroyed nothing, under a message that
  // implied a partial success.
  const { component, api, ctx } = browser({ count: 250 });
  const sizes = [];
  api.purgeEntries = (uuids) => {
    sizes.push(uuids.length);
    return Promise.resolve({ destroyed: uuids });
  };

  return component.applyTo('delete_forever', component.selectedEntries()).then(() => {
    assert.deepEqual(Array.from(sizes), [ctx.VAULT_PURGE_BATCH_SIZE, 50]);
  });
});

test('the question counts the stored rows, not the ones that opened', async () => {
  // A tampered row never reaches `entries`, and the server destroys it with
  // the rest. Asking about three and destroying five would hide exactly the
  // rows a user has most reason to keep.
  const { component } = browser({ count: 3, unreadable: 2 });
  let asked = '';
  component.confirm = (message) => {
    asked = message;
    return Promise.resolve(false);
  };

  await component.emptyTrash();

  assert.match(asked, /Permanently delete the 5 entries in the trash\?/);
  assert.match(asked, /2 of them cannot be read on this device\./);
});

test('the question is grammatical for a single entry', async () => {
  const { component } = browser({ count: 1 });
  let asked = '';
  component.confirm = (message) => {
    asked = message;
    return Promise.resolve(false);
  };

  await component.emptyTrash();

  assert.match(asked, /Permanently delete the entry in the trash\?/);
  assert.ok(!/cannot be read/.test(asked));
});

test('the question is grammatical when the single entry did not open', async () => {
  const { component } = browser({ count: 0, unreadable: 1 });
  let asked = '';
  component.confirm = (message) => {
    asked = message;
    return Promise.resolve(false);
  };

  await component.emptyTrash();

  assert.match(asked, /Permanently delete the entry in the trash\?/);
  assert.match(asked, /It cannot be read on this device\./);
});

test('declining the question destroys nothing', async () => {
  const { component, api } = browser();
  component.confirm = () => Promise.resolve(false);
  api.purgeVaultTrash = () => {
    throw new Error('a declined confirmation must send no request');
  };

  await component.emptyTrash();
});
