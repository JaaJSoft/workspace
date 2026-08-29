// The browser shell: what it loads when a vault is opened, and what it
// refuses to decrypt while loading it. The second half is the security
// property - a listing that opened a password would put one in component
// state, where the developer tools show it - so the count of decryptions is
// asserted rather than described.
const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

const VAULT_UUID = '11111111-1111-7111-8111-111111111111';
const OTHER_UUID = '22222222-2222-7222-8222-222222222222';

function vaultRow(uuid, name) {
  return {
    uuid: uuid,
    owner_account_uuid: 'account-1',
    encrypted_name: 'ct:' + name,
    icon: 'lock',
    color: 'primary',
    is_favorite: false,
    key_version: 1,
    metadata_sig: 'sig',
    wrapped_key: 'AQ',
  };
}

function entryRow(uuid) {
  return {
    uuid: uuid,
    vault: VAULT_UUID,
    type: 'login',
    folder: null,
    tags: [],
    is_favorite: false,
    encrypted_name: 'ct:name',
    encrypted_notes: '',
    key_version: 1,
    entry_version: 1,
    metadata_sig: 'sig',
    deleted_at: null,
    updated_at: '2026-08-28',
    created_at: '2026-08-01',
    entry_fields: [
      { field_id: 'username', encrypted_value: 'ct:username' },
      { field_id: 'password', encrypted_value: 'ct:password' },
    ],
  };
}

// The page hands the controller its server-rendered data through
// <script type="application/json"> blocks, so the stub answers by id exactly
// as the DOM does.
function jsonScripts(data) {
  return {
    getElementById: (id) =>
      Object.prototype.hasOwnProperty.call(data, id)
        ? { textContent: JSON.stringify(data[id]) }
        : null,
    addEventListener() {},
  };
}

function browser(options = {}) {
  const opened = [];
  const entryKeys = [];
  const locks = [];
  const ctx = loadScripts(
    [
      'workspace/vault/ui/static/vault/ui/js/vault_unlock.js',
      'workspace/vault/ui/static/vault/ui/js/vault_store.js',
      'workspace/vault/ui/static/vault/ui/js/vault_reader.js',
      'workspace/vault/ui/static/vault/ui/js/vault_browser.js',
    ],
    {
      TextEncoder: globalThis.TextEncoder,
      TextDecoder: globalThis.TextDecoder,
      URLSearchParams: globalThis.URLSearchParams,
      document: jsonScripts({
        'vault-uuid': VAULT_UUID,
        'entry-types': [{ id: 'login', label: 'Login', icon: 'key-round', fields: [] }],
        ...options.data,
      }),
      location: { search: options.search || '' },
      localStorage: {
        values: {},
        getItem(key) {
          return Object.prototype.hasOwnProperty.call(this.values, key)
            ? this.values[key]
            : null;
        },
        setItem(key, value) { this.values[key] = String(value); },
        removeItem(key) { delete this.values[key]; },
      },
      addEventListener() {},
      vaultSession: {
        isUnlocked: () => true,
        unlock: async () => {},
        lock() { locks.push('lock'); },
        onLock() {},
        onTick() {},
        watchForIdle() {},
        secondsUntilLock: () => 300,
        rememberedSecret: () => null,
        forgetDevice() {},
        accountUuid: () => 'account-1',
        openVaultKey: async () => new Uint8Array(32),
        openEntryKey: async (vaultUuid, wrapped, entryUuid) => {
          entryKeys.push(entryUuid);
          return new Uint8Array(32);
        },
        verifyRecord: async () => {},
        verifyVaultMetadata: async () => {},
        ...options.session,
      },
      vaultApi: {
        listVaults: async () => [vaultRow(VAULT_UUID, 'Personal')],
        listFolders: async () => [],
        listTags: async () => [],
        listEntries: async () => [],
        fetchVaultActions: async () => ({}),
        fetchEntryActions: async () => ({}),
        ...options.api,
      },
      vaultCrypto: {
        fromBase64Url: (value) => value,
        open: async (key, ciphertext, ad) => {
          opened.push(ad);
          return new TextEncoder().encode('open:' + ad);
        },
        AD: {
          vaultFieldAd: (uuid, field) => 'vault:' + uuid + '|' + field,
          entryFieldAd: (uuid, field) => uuid + '|' + field,
          folderFieldAd: (uuid, field) => 'folder:' + uuid + '|' + field,
          tagFieldAd: (uuid, field) => 'tag:' + uuid + '|' + field,
        },
        vaultMetadataPayload: (fields) => fields,
        entryMetadataPayload: (fields) => fields,
        folderMetadataPayload: (fields) => fields,
        tagMetadataPayload: (fields) => fields,
        ...options.crypto,
      },
    },
  );
  return { component: ctx.vaultBrowser(), opened, entryKeys, locks, ctx };
}

test('the browser reads the vault it was routed to from the page', () => {
  const { component } = browser();
  component.init();
  assert.equal(component.vaultUuid, VAULT_UUID);
});

test('opening a vault loads its folders, its tags and its entries', async () => {
  const asked = [];
  const { component } = browser({
    api: {
      listFolders: async (uuid) => { asked.push('folders:' + uuid); return []; },
      listTags: async (uuid) => { asked.push('tags:' + uuid); return []; },
      listEntries: async (uuid, opts) => {
        asked.push('entries:' + uuid + (opts && opts.trashed ? ':trashed' : ''));
        return [];
      },
    },
  });
  component.init();
  await component.load();
  // The trash is a view over the same vault, so its rows arrive with the
  // rest: switching to it must not cost a round trip.
  assert.deepStrictEqual(Array.from(asked).sort(), [
    'entries:' + VAULT_UUID,
    'entries:' + VAULT_UUID + ':trashed',
    'folders:' + VAULT_UUID,
    'tags:' + VAULT_UUID,
  ]);
});

test('a listing opens a name and a login, and never a password', async () => {
  const rows = [entryRow('e-1'), entryRow('e-2'), entryRow('e-3')];
  const { component, opened, entryKeys } = browser({
    api: { listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : rows) },
  });
  component.init();
  await component.load();

  assert.equal(component.entries.length, 3);
  // Two per entry, no more: the name and the login. A third would mean a
  // secret was opened to render a row.
  const entryFields = opened.filter((ad) => !String(ad).startsWith('vault:'));
  assert.ok(entryFields.length <= 2 * rows.length, 'opened ' + entryFields.length + ' fields');
  assert.deepStrictEqual(Array.from(entryKeys), ['e-1', 'e-2', 'e-3']);
  assert.ok(!opened.some((ad) => String(ad).endsWith('|password')));
  assert.ok(component.entries.every((entry) => !('password' in entry)));
});

test('an entry whose signature does not verify is counted, never listed', async () => {
  const { component } = browser({
    api: { listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryRow('e-1')]) },
    session: { verifyRecord: async () => { throw new Error('forged'); } },
  });
  component.init();
  await component.load();
  assert.deepStrictEqual(Array.from(component.entries), []);
  assert.equal(component.tamperedCount, 1);
});

test('the switcher lists every vault and marks the one that is open', async () => {
  const { component } = browser({
    api: {
      listVaults: async () => [vaultRow(VAULT_UUID, 'Personal'), vaultRow(OTHER_UUID, 'Work')],
    },
  });
  component.init();
  await component.load();
  assert.deepStrictEqual(
    Array.from(component.vaults, (vault) => vault.uuid),
    [VAULT_UUID, OTHER_UUID],
  );
  assert.equal(component.openVault.uuid, VAULT_UUID);
  assert.equal(component.openVault.name, 'open:vault:' + VAULT_UUID + '|name');
});

test('a vault owned by another account is shown as a membership', async () => {
  const row = Object.assign(vaultRow(VAULT_UUID, 'Shared'), {
    owner_account_uuid: 'account-2',
  });
  const { component } = browser({ api: { listVaults: async () => [row] } });
  component.init();
  await component.load();
  assert.equal(component.isMember(component.openVault), true);
});

test('a vault the account cannot reach leaves the browser with nothing open', async () => {
  // The server never answers 404 for one, so the page is what has to say it:
  // the UUID names a vault this account holds no key for, or none at all.
  const { component } = browser({ api: { listVaults: async () => [] } });
  component.init();
  await component.load();
  assert.equal(component.openVault, null);
  assert.equal(component.missing, true);
});

test('the lock command locks the account without needing a vault', () => {
  const { component, locks } = browser({ search: '?action=lock' });
  component.init();
  assert.deepStrictEqual(Array.from(locks), ['lock']);
});

test('the new-entry command is remembered until a dialog can honour it', () => {
  const { component } = browser({ search: '?action=new' });
  component.init();
  assert.equal(component.pendingNewEntry, true);
});

test('no command means nothing is locked and nothing is pending', () => {
  const { component, locks } = browser();
  component.init();
  assert.deepStrictEqual(Array.from(locks), []);
  assert.equal(component.pendingNewEntry, false);
});

test('the New menu names the type it was asked for', () => {
  const { component } = browser();
  component.init();
  component.newEntry('login');
  assert.equal(component.pendingNewEntry, true);
  assert.equal(component.draftType, 'login');
});

test('a lock drops a creation nobody has confirmed', () => {
  const listeners = [];
  const { component } = browser({ session: { onLock: (cb) => listeners.push(cb) } });
  component.init();
  component.newEntry('login');
  component.newFolder();
  listeners.forEach((callback) => callback());
  assert.equal(component.pendingNewEntry, false);
  assert.equal(component.pendingNewFolder, false);
  assert.equal(component.draftType, null);
});

test('the collapsed sidebar survives a reload', () => {
  const { component, ctx } = browser();
  component.init();
  assert.equal(component.collapsed, false);
  component.toggleCollapsed();
  assert.equal(component.collapsed, true);
  const second = ctx.vaultBrowser();
  second.init();
  assert.equal(second.collapsed, true);
});

test('the entry types come from the server, never from the client', () => {
  const { component } = browser();
  component.init();
  assert.deepStrictEqual(
    Array.from(component.entryTypes, (type) => type.id),
    ['login'],
  );
});

test('a lock empties everything the browser had opened', async () => {
  const listeners = [];
  const { component } = browser({
    api: { listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryRow('e-1')]) },
    session: { onLock: (callback) => listeners.push(callback) },
  });
  component.init();
  await component.load();
  assert.equal(component.entries.length, 1);
  listeners.forEach((callback) => callback());
  assert.deepStrictEqual(Array.from(component.entries), []);
  assert.deepStrictEqual(Array.from(component.folders), []);
  assert.deepStrictEqual(Array.from(component.tags), []);
  assert.equal(component.openVault, null);
  assert.equal(component.state, 'locked');
});

test('the heading names the folder being looked at', async () => {
  const { component } = browser({
    api: {
      listFolders: async () => [
        {
          uuid: 'f-1',
          vault: VAULT_UUID,
          parent: null,
          position: 0,
          encrypted_name: 'ct',
          metadata_sig: 'sig',
        },
      ],
    },
  });
  component.init();
  await component.load();
  assert.equal(component.heading(), 'All entries');
  component.openFolder('f-1');
  assert.equal(component.heading(), 'open:folder:f-1|name');
  component.setView('trash');
  assert.equal(component.heading(), 'Trash');
});
