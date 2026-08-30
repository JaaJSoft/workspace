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

// A vault whose signature the account refuses: readVault strips its name and
// flags it, which is what makes it unfit to land on.
function forgedVaultRow(uuid) {
  return Object.assign(vaultRow(uuid, 'Forged'), { metadata_sig: 'forged' });
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
  const copied = [];
  const visited = [];
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
      history: {
        replaced: [],
        replaceState(state, title, url) { this.replaced.push(url); },
      },
      TAG_CHIP_COLORS: [
        { name: 'None', value: '' },
        { name: 'Red', value: '#ef4444' },
      ],
      open: (url) => { visited.push(url); },
      setInterval: () => 1,
      clearInterval() {},
      navigator: {
        clipboard: {
          writeText: async (text) => { copied.push(text); },
          readText: async () => copied[copied.length - 1],
        },
      },
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
        setIdleTimeout() {},
        verifyRecord: async () => {},
        verifyVaultMetadata: async () => {},
        sign: async () => 'signature',
        ...options.session,
      },
      vaultApi: {
        createTag: async () => ({}),
        listVaults: async () =>
          options.vaults || [vaultRow(VAULT_UUID, 'Personal')],
        listFolders: async () => [],
        listTags: async () => [],
        listEntries: async () => [],
        fetchVaultActions: async () => ({}),
        fetchEntryActions: async () => ({}),
        ...options.api,
      },
      vaultCrypto: {
        uuidV7: () => 'entry-uuid',
        fromBase64Url: (value) => value,
        toBase64Url: (value) => 'b64',
        seal: async () => new Uint8Array(4),
        KDF_HKDF_SHA256: 0x01,
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
  return { component: ctx.vaultBrowser(), opened, entryKeys, locks, copied, visited, ctx, options };
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

test('a lock drops a creation nobody has confirmed', () => {
  const listeners = [];
  const { component } = browser({ session: { onLock: (cb) => listeners.push(cb) } });
  component.init();
  component.newEntry('login');
  component.newFolder();
  listeners.forEach((callback) => callback());
  assert.strictEqual(component.draft, null);
  assert.strictEqual(component.folderDraft, null);
  assert.strictEqual(component.pendingNewEntry, false);
});

test('the tile size survives a reload, and belongs to this screen alone', () => {
  // The collapse is vaultSidebar's business; what this screen stores for
  // itself is how big its tiles are, on this device rather than on the
  // account.
  const { component, ctx } = browser();
  component.init();
  assert.equal(component.tileSize, 3);
  component.setTileSize(5);
  const second = ctx.vaultBrowser();
  second.init();
  assert.equal(second.tileSize, 5);
  assert.equal(ctx.localStorage.getItem('vault.browser.tileSize'), '5');
});

test('the view mode survives a reload', () => {
  const { component, ctx } = browser();
  component.init();
  assert.equal(component.viewMode, 'list');
  component.setViewMode('mosaic');
  const second = ctx.vaultBrowser();
  second.init();
  assert.equal(second.viewMode, 'mosaic');
  assert.equal(ctx.localStorage.getItem('vault.browser.viewMode'), 'mosaic');
});

test('a tile size off the scale is refused rather than drawn', () => {
  const { component } = browser();
  component.init();
  component.setTileSize(9);
  assert.equal(component.tileSize, 3);
  assert.equal(component.tileMinWidth(), 180);
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

test('a lock drops every draft holding typed-in plaintext', async () => {
  // A tag name is plaintext the user typed, so it goes with the keys. Left
  // behind it also leaves the dialog on screen over a locked vault, with a
  // Create button that returns early and does nothing.
  const listeners = [];
  const { component } = browser({ session: { onLock: (callback) => listeners.push(callback) } });
  component.init();
  component.newTag();
  assert.notEqual(component.tagDraft, null);
  listeners.forEach((callback) => callback());
  assert.equal(component.tagDraft, null);
  assert.equal(component.draft, null);
  assert.equal(component.folderDraft, null);
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

// --- the listing, its selection and the bar the selection raises -----------

function entryWith(uuid, overrides = {}) {
  return Object.assign(entryRow(uuid), overrides);
}

const ACTION = {
  edit: { id: 'edit', label: 'Edit', icon: 'pencil', bulk: false, css_class: '' },
  // Offered by the registry and not implemented by the browser, which is what
  // several tests here use it for.
  move: { id: 'move', label: 'Move to folder', icon: 'folder', bulk: true, css_class: '' },
  trash: { id: 'trash', label: 'Move to trash', icon: 'trash-2', bulk: true, css_class: 'text-error' },
  favorite: { id: 'favorite', label: 'Add to favourites', icon: 'star', bulk: true, css_class: '' },
  unfavorite: { id: 'unfavorite', label: 'Remove from favourites', icon: 'star-off', bulk: true, css_class: '' },
  copy_password: { id: 'copy_password', label: 'Copy password', icon: 'key-round', bulk: false, css_class: '' },
};

function ids(actions) {
  return Array.from(actions, (action) => action.id);
}

// A lock notifies its listeners synchronously, and one of them takes the
// secret back off the clipboard - which reads it back before wiping it, so
// the wipe lands a few microtasks later than the callback returns.
async function settle(turns = 4) {
  for (let i = 0; i < turns; i += 1) await Promise.resolve();
}

test('the bulk bar offers only what every selected row offers', async () => {
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) =>
        opts && opts.trashed ? [] : [entryWith('e-1'), entryWith('e-2')],
      fetchEntryActions: async () => ({
        'e-1': [ACTION.edit, ACTION.favorite, ACTION.trash, ACTION.copy_password],
        // No trash on this one: a member whose wrap was revoked mid-listing
        // is the realistic case, and the bar must not offer what one row
        // would refuse.
        'e-2': [ACTION.edit, ACTION.favorite],
      }),
    },
  });
  component.init();
  await component.load();
  component.toggleSelection('e-1');
  component.toggleSelection('e-2');
  assert.deepStrictEqual(ids(component.bulkActions()), ['favorite']);
});

test('a bulk action the registry marks single-row is never offered in bulk', async () => {
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
      fetchEntryActions: async () => ({
        'e-1': [ACTION.edit, ACTION.copy_password, ACTION.favorite],
      }),
    },
  });
  component.init();
  await component.load();
  component.toggleSelection('e-1');
  assert.deepStrictEqual(ids(component.bulkActions()), ['favorite']);
});

test('favorite goes when every selected row is already one', async () => {
  const both = [ACTION.favorite, ACTION.unfavorite];
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) =>
        opts && opts.trashed
          ? []
          : [
              entryWith('e-1', { is_favorite: true }),
              entryWith('e-2', { is_favorite: true }),
            ],
      fetchEntryActions: async () => ({ 'e-1': both, 'e-2': both }),
    },
  });
  component.init();
  await component.load();
  component.toggleSelection('e-1');
  component.toggleSelection('e-2');
  assert.deepStrictEqual(ids(component.bulkActions()), ['unfavorite']);
});

test('a mixed selection is offered both favourite verbs', async () => {
  // Intersecting the filtered lists would drop both: one row offers only
  // favorite, the other only unfavorite. The rule is applied to the whole
  // selection, not row by row.
  const both = [ACTION.favorite, ACTION.unfavorite];
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) =>
        opts && opts.trashed
          ? []
          : [entryWith('e-1', { is_favorite: true }), entryWith('e-2')],
      fetchEntryActions: async () => ({ 'e-1': both, 'e-2': both }),
    },
  });
  component.init();
  await component.load();
  component.toggleSelection('e-1');
  component.toggleSelection('e-2');
  assert.deepStrictEqual(ids(component.bulkActions()), ['favorite', 'unfavorite']);
});

test('a single row is offered one favourite verb, decided by the row', async () => {
  const both = [ACTION.favorite, ACTION.unfavorite];
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) =>
        opts && opts.trashed ? [] : [entryWith('e-1', { is_favorite: true })],
      fetchEntryActions: async () => ({ 'e-1': both }),
    },
  });
  component.init();
  await component.load();
  assert.deepStrictEqual(ids(component.actionsFor(component.entries[0])), ['unfavorite']);
});

test('nothing selected raises no bar', async () => {
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
      fetchEntryActions: async () => ({ 'e-1': [ACTION.move] }),
    },
  });
  component.init();
  await component.load();
  assert.deepStrictEqual(ids(component.bulkActions()), []);
});

test('an action list that lands after the vault reloaded is dropped', async () => {
  // Two listings can be in flight at once, and the slower answer must not
  // describe rows that left the screen.
  let resolveFirst;
  let call = 0;
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
      fetchEntryActions: () => {
        call += 1;
        if (call === 1) return new Promise((resolve) => { resolveFirst = resolve; });
        return Promise.resolve({ 'e-1': [ACTION.move] });
      },
    },
  });
  component.init();
  const first = component.load();
  await component.load();
  resolveFirst({ 'e-1': [ACTION.edit, ACTION.move, ACTION.trash] });
  await first;
  assert.deepStrictEqual(ids(component.entryActions['e-1'] || []), ['move']);
});

test('clicking a folder enters it and clicking an entry opens the panel', async () => {
  const { component } = browser({
    api: {
      listFolders: async () => [
        { uuid: 'f-1', vault: VAULT_UUID, parent: null, position: 0,
          encrypted_name: 'ct', metadata_sig: 'sig' },
      ],
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
    },
  });
  component.init();
  await component.load();

  component.openFolderFromRow(component.folders[0]);
  assert.equal(component.folderUuid, 'f-1');
  assert.equal(component.panelEntry, null);

  component.openEntryFromRow(component.entries[0]);
  assert.equal(component.panelEntry.uuid, 'e-1');
  // The panel is not a selection: the checkbox owns that, as in files.
  assert.deepStrictEqual(Array.from(component.selected), []);
});

test('leaving a folder closes the panel it was opened from', async () => {
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
    },
  });
  component.init();
  await component.load();
  component.openEntryFromRow(component.entries[0]);
  component.setView('trash');
  assert.equal(component.panelEntry, null);
});

test('a row is labelled and drawn from the type catalogue', () => {
  const { component } = browser();
  component.init();
  assert.equal(component.typeLabel('login'), 'Login');
  assert.equal(component.typeIcon('login'), 'key-round');
});

test('a type no catalogue entry claims still renders a row', () => {
  // `type` is a Python-side choice, so nothing stops a stored row from naming
  // a type no proxy claims. It must cost that row its label, not the listing.
  const { component } = browser();
  component.init();
  assert.equal(component.typeLabel('passport'), 'passport');
  assert.ok(component.typeIcon('passport'));
});

test('an unreadable timestamp shows a dash rather than "Invalid Date"', () => {
  const { component } = browser();
  component.init();
  assert.equal(component.shortDate(''), '-');
  assert.equal(component.shortDate('not a date'), '-');
  assert.ok(component.shortDate('2026-08-28T10:00:00Z').length > 3);
});

// --- the menu, built by the server and by nothing else ---------------------

test('the menu renders the ids the server returned, in the order it returned them', async () => {
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
      fetchEntryActions: async () => ({
        'e-1': [ACTION.copy_password, ACTION.edit, ACTION.trash],
      }),
    },
  });
  component.init();
  await component.load();
  await component.openMenu({ clientX: 10, clientY: 20 }, component.entries[0]);
  assert.deepStrictEqual(ids(component.menuActions()), ['copy_password', 'edit', 'trash']);
  assert.equal(component.menu.open, true);
});

test('an empty answer renders an empty menu rather than a default one', async () => {
  // An entry the caller cannot reach comes back with an empty list, never a
  // missing key. Falling back to "the usual actions" would put a menu in
  // front of a row the server is about to refuse every request for.
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
      fetchEntryActions: async () => ({ 'e-1': [] }),
    },
  });
  component.init();
  await component.load();
  await component.openMenu({ clientX: 0, clientY: 0 }, component.entries[0]);
  assert.deepStrictEqual(ids(component.menuActions()), []);
});

test('an answer that lands after the menu moved on is thrown away', async () => {
  let resolveFirst;
  let call = 0;
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) =>
        opts && opts.trashed ? [] : [entryWith('e-1'), entryWith('e-2')],
      fetchEntryActions: (uuids) => {
        call += 1;
        // The listing's own batch, then one refresh per menu opening.
        if (call === 1) return Promise.resolve({ 'e-1': [], 'e-2': [] });
        if (call === 2) return new Promise((resolve) => { resolveFirst = resolve; });
        return Promise.resolve({ 'e-2': [ACTION.edit] });
      },
    },
  });
  component.init();
  await component.load();
  const stale = component.openMenu({ clientX: 0, clientY: 0 }, component.entries[0]);
  await component.openMenu({ clientX: 0, clientY: 0 }, component.entries[1]);
  resolveFirst({ 'e-1': [ACTION.trash, ACTION.move] });
  await stale;
  assert.equal(component.menu.entry.uuid, 'e-2');
  assert.deepStrictEqual(ids(component.menuActions()), ['edit']);
});

test('the menu shuts when the listing under it changes', async () => {
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
      fetchEntryActions: async () => ({ 'e-1': [ACTION.edit] }),
    },
  });
  component.init();
  await component.load();

  await component.openMenu({ clientX: 0, clientY: 0 }, component.entries[0]);
  component.setView('trash');
  assert.equal(component.menu.open, false);

  await component.openMenu({ clientX: 0, clientY: 0 }, component.entries[0]);
  component.closeMenu();
  assert.equal(component.menu.open, false);
});

test('a lock shuts the menu on whatever it was describing', async () => {
  const listeners = [];
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
      fetchEntryActions: async () => ({ 'e-1': [ACTION.edit] }),
    },
    session: { onLock: (callback) => listeners.push(callback) },
  });
  component.init();
  await component.load();
  await component.openMenu({ clientX: 0, clientY: 0 }, component.entries[0]);
  listeners.forEach((callback) => callback());
  assert.equal(component.menu.open, false);
  assert.equal(component.menu.entry, null);
});

test('the panel answers what the registry allows for the row it shows', async () => {
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) =>
        opts && opts.trashed ? [] : [entryWith('e-1'), entryWith('e-2')],
      fetchEntryActions: async () => ({
        'e-1': [ACTION.edit, ACTION.copy_password],
        'e-2': [],
      }),
    },
  });
  component.init();
  await component.load();
  component.openEntryFromRow(component.entries[0]);
  assert.equal(component.panelHasAction('edit'), true);
  assert.equal(component.panelHasAction('trash'), false);
  component.openEntryFromRow(component.entries[1]);
  assert.equal(component.panelHasAction('edit'), false);
});

test('the panel says which fields a row carries without opening one', async () => {
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
    },
  });
  component.init();
  await component.load();
  component.openEntryFromRow(component.entries[0]);
  assert.equal(component.panelCarries('password'), true);
  assert.equal(component.panelCarries('totp'), false);
  // Knowing a password is there is not holding it: nothing opened it.
  assert.ok(!('password' in component.panelEntry));
});

// --- the entry form, driven by the type registry ---------------------------

const LOGIN_TYPE = {
  id: 'login',
  label: 'Login',
  icon: 'key-round',
  fields: [
    { field_id: 'username', label: 'Username', secret: false, generator: false, kind: 'text' },
    { field_id: 'password', label: 'Password', secret: true, generator: true, kind: 'text' },
    { field_id: 'totp', label: 'Authenticator key', secret: true, generator: false, kind: 'totp' },
    { field_id: 'uri', label: 'Website', secret: false, generator: false, kind: 'text' },
  ],
};

function typed(options = {}) {
  return browser({ data: { 'entry-types': [LOGIN_TYPE] }, ...options });
}

test('the form renders one input per declared field, minus the authenticator key', () => {
  // The TOTP field is a shared secret, not a value to type into a box, and
  // what it needs is the next issue's. Rendering it here would offer to
  // overwrite one with whatever was typed.
  const { component } = typed();
  component.init();
  component.newEntry('login');
  assert.deepStrictEqual(
    Array.from(component.formFields(), (field) => field.field_id),
    ['username', 'password', 'uri'],
  );
});

test('a new entry starts empty, with a fresh uuid and the current folder', async () => {
  const { component } = typed();
  component.init();
  await component.load();
  component.openFolder('f-1');
  component.newEntry('login');
  assert.equal(component.draft.name, '');
  assert.equal(component.draft.folder, 'f-1');
  assert.equal(component.draft.uuid, 'entry-uuid');
  assert.deepStrictEqual({ ...component.draft.values }, {});
});

test('editing loads the row and opens only the fields the form shows', async () => {
  const { component } = typed({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
      fetchEntryActions: async () => ({ 'e-1': [ACTION.edit] }),
    },
    session: {
      openEntryKey: async () => new Uint8Array(32),
    },
  });
  component.init();
  await component.load();
  await component.editEntry(component.entries[0]);
  assert.equal(component.draft.uuid, 'e-1');
  assert.equal(component.draft.name, 'open:e-1|name');
  // The row carries a username and a password; both are form fields, so both
  // are opened - and nothing else is.
  assert.deepStrictEqual(Object.keys({ ...component.draft.values }).sort(), [
    'password',
    'username',
  ]);
});

test('editing carries the notes ciphertext without opening it', async () => {
  // No form here edits notes, and the write is a full signed replacement: a
  // draft that dropped the column would erase stored notes on the first
  // rename. Carrying the ciphertext keeps them without decrypting anything.
  const { component, opened } = typed({
    api: {
      listEntries: async (uuid, opts) =>
        opts && opts.trashed ? [] : [entryWith('e-1', { encrypted_notes: 'ct:notes' })],
      fetchEntryActions: async () => ({ 'e-1': [ACTION.edit] }),
    },
    session: { openEntryKey: async () => new Uint8Array(32) },
  });
  component.init();
  await component.load();
  await component.editEntry(component.entries[0]);
  assert.equal(component.draft.encryptedNotes, 'ct:notes');
  assert.equal(component.draft.notes, '');
  assert.ok(
    !Array.from(opened).some((entry) => String(entry).includes('notes')),
    'the notes were never opened',
  );
});

test('saving a new entry posts, saving an edit puts', async () => {
  const calls = [];
  const { component } = typed({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
      createEntry: async (body) => { calls.push(['post', body]); return {}; },
      updateEntry: async (uuid, body) => { calls.push(['put:' + uuid, body]); return {}; },
    },
  });
  component.init();
  await component.load();

  component.newEntry('login');
  component.draft.name = 'Fresh';
  await component.saveEntry();
  assert.equal(calls[0][0], 'post');

  await component.editEntry(component.entries[0]);
  await component.saveEntry();
  assert.equal(calls[1][0], 'put:e-1');
});

test('the saved body carries every signed field', async () => {
  const calls = [];
  const { component } = typed({
    api: { createEntry: async (body) => { calls.push(body); return {}; } },
  });
  component.init();
  await component.load();
  component.newEntry('login');
  component.draft.name = 'Fresh';
  component.draft.values.username = 'jc';
  await component.saveEntry();
  const body = calls[0];
  assert.deepStrictEqual(Object.keys(body).sort(), [
    'encrypted_name',
    'encrypted_notes',
    'fields',
    'is_favorite',
    'metadata_sig',
    'tags',
    'type',
    'uuid',
    'vault',
  ].concat(['folder']).sort());
});

test('an entry with no name is never written', async () => {
  // The name is the only thing a listing shows; one without is a row the user
  // cannot tell from another.
  const calls = [];
  const { component } = typed({
    api: { createEntry: async (body) => { calls.push(body); return {}; } },
  });
  component.init();
  await component.load();
  component.newEntry('login');
  component.draft.name = '   ';
  await component.saveEntry();
  assert.deepStrictEqual(Array.from(calls), []);
});

test('a lock closes the form and writes nothing', async () => {
  const listeners = [];
  const { component } = typed({ session: { onLock: (cb) => listeners.push(cb) } });
  component.init();
  await component.load();
  component.newEntry('login');
  listeners.forEach((callback) => callback());
  assert.strictEqual(component.draft, null);
});

// --- copying a secret ------------------------------------------------------

test('copying a password opens that field and nothing else', async () => {
  const { component, opened, copied } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
      fetchEntryActions: async () => ({ 'e-1': [ACTION.copy_password] }),
    },
  });
  component.init();
  await component.load();
  const beforeCopy = opened.length;
  await component.runAction(ACTION.copy_password, component.entries[0]);
  assert.deepStrictEqual(opened.slice(beforeCopy), ['e-1|password']);
  assert.deepStrictEqual(Array.from(copied), ['open:e-1|password']);
  // Opened, handed over, dropped: it never lands in component state.
  assert.ok(!('password' in component.entries[0]));
});

test('an action the registry did not offer is refused even from a stale menu', async () => {
  const { component, copied } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
      fetchEntryActions: async () => ({ 'e-1': [ACTION.edit] }),
    },
  });
  component.init();
  await component.load();
  await component.runAction(ACTION.copy_password, component.entries[0]);
  assert.deepStrictEqual(Array.from(copied), []);
});

test('a stored address that is not a web address is never opened', async () => {
  // The destination comes out of the vault, so a javascript: or data: value
  // saved there would be a saved address that runs as code on this page.
  const { component, visited } = browser({
    api: {
      listEntries: async (uuid, opts) =>
        opts && opts.trashed
          ? []
          : [
              Object.assign(entryWith('e-1'), {
                entry_fields: [{ field_id: 'uri', encrypted_value: 'ct' }],
              }),
            ],
      fetchEntryActions: async () => ({ 'e-1': [{ id: 'open_uri', bulk: false }] }),
    },
    crypto: {
      open: async () => new TextEncoder().encode('javascript:alert(1)'),
    },
  });
  component.init();
  await component.load();
  await component.runAction({ id: 'open_uri' }, component.entries[0]);
  assert.deepStrictEqual(Array.from(visited), []);
  assert.match(component.error, /web address/i);
});

test('a lock takes the secret back off the clipboard', async () => {
  const listeners = [];
  const { component, copied } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
      fetchEntryActions: async () => ({ 'e-1': [ACTION.copy_password] }),
    },
    session: { onLock: (callback) => listeners.push(callback) },
  });
  component.init();
  await component.load();
  await component.runAction(ACTION.copy_password, component.entries[0]);
  listeners.forEach((callback) => callback());
  await settle();
  // The last write wins, and it is the empty one.
  assert.equal(copied[copied.length - 1], '');
});

// --- the trash -------------------------------------------------------------

const TRASH_ACTIONS = {
  restore: { id: 'restore', label: 'Restore', icon: 'undo-2', bulk: true, css_class: '' },
  delete_forever: {
    id: 'delete_forever', label: 'Delete for good', icon: 'trash-2',
    bulk: true, css_class: 'text-error',
  },
};

function trashed(uuid) {
  return Object.assign(entryRow(uuid), { deleted_at: '2026-08-28T09:00:00Z' });
}

test('the trash lists only the rows the server marked deleted', async () => {
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) =>
        opts && opts.trashed ? [trashed('e-2')] : [entryWith('e-1')],
    },
  });
  component.init();
  await component.load();
  component.setView('trash');
  assert.deepStrictEqual(
    Array.from(component.visibleEntries(), (entry) => entry.uuid),
    ['e-2'],
  );
  // A folder is never in the trash: deleting one is immediate and composite.
  assert.deepStrictEqual(Array.from(component.visibleFolders()), []);
});

test('restoring sends no signature and asks for nothing', async () => {
  // deleted_at is outside the signed payload, so there is nothing to re-sign
  // and nothing to verify - which is also what makes it idempotent.
  const calls = [];
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [trashed('e-1')] : []),
      fetchEntryActions: async () => ({ 'e-1': [TRASH_ACTIONS.restore] }),
      restoreEntry: async (uuid) => { calls.push(uuid); return {}; },
    },
  });
  component.init();
  await component.load();
  component.setView('trash');
  await component.runAction(TRASH_ACTIONS.restore, component.entries[0]);
  assert.deepStrictEqual(Array.from(calls), ['e-1']);
});

test('destroying an entry asks first, and a refusal writes nothing', async () => {
  const calls = [];
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [trashed('e-1')] : []),
      fetchEntryActions: async () => ({ 'e-1': [TRASH_ACTIONS.delete_forever] }),
      purgeEntry: async (uuid) => { calls.push(uuid); return {}; },
    },
  });
  component.init();
  await component.load();
  component.setView('trash');

  component.confirm = async () => false;
  await component.runAction(TRASH_ACTIONS.delete_forever, component.entries[0]);
  assert.deepStrictEqual(Array.from(calls), []);

  component.confirm = async () => true;
  await component.runAction(TRASH_ACTIONS.delete_forever, component.entries[0]);
  assert.deepStrictEqual(Array.from(calls), ['e-1']);
});

test('trashing an entry is a delete, not a rewrite', async () => {
  const calls = [];
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
      fetchEntryActions: async () => ({ 'e-1': [ACTION.trash] }),
      trashEntry: async (uuid) => { calls.push(uuid); return null; },
      updateEntry: async () => { throw new Error('the trash must not re-sign'); },
    },
  });
  component.init();
  await component.load();
  await component.runAction(ACTION.trash, component.entries[0]);
  assert.deepStrictEqual(Array.from(calls), ['e-1']);
});

test('a bulk action runs over every selected row', async () => {
  const calls = [];
  let asked = 0;
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) =>
        opts && opts.trashed ? [] : [entryWith('e-1'), entryWith('e-2')],
      fetchEntryActions: async () => ({
        'e-1': [ACTION.trash],
        'e-2': [ACTION.trash],
      }),
      trashEntry: async (uuid) => { calls.push(uuid); return null; },
    },
  });
  component.init();
  await component.load();
  component.confirm = async () => { asked += 1; return true; };
  component.toggleSelection('e-1');
  component.toggleSelection('e-2');
  await component.runBulkAction(ACTION.trash);
  assert.deepStrictEqual(Array.from(calls).sort(), ['e-1', 'e-2']);
  // The trash is reversible, so it does not stop and ask. Asking about a
  // reversible thing is what teaches people to click through the question
  // that matters.
  assert.equal(asked, 0);
});

test('destroying a batch asks once, not once per row', async () => {
  const calls = [];
  let asked = 0;
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) =>
        opts && opts.trashed ? [trashed('e-1'), trashed('e-2')] : [],
      fetchEntryActions: async () => ({
        'e-1': [TRASH_ACTIONS.delete_forever],
        'e-2': [TRASH_ACTIONS.delete_forever],
      }),
      purgeEntry: async (uuid) => { calls.push(uuid); return {}; },
    },
  });
  component.init();
  await component.load();
  component.setView('trash');
  component.confirm = async () => { asked += 1; return true; };
  component.toggleSelection('e-1');
  component.toggleSelection('e-2');
  await component.runBulkAction(TRASH_ACTIONS.delete_forever);
  assert.deepStrictEqual(Array.from(calls).sort(), ['e-1', 'e-2']);
  assert.equal(asked, 1);
});

test('a bulk action stops at the first refusal rather than half-finishing quietly', async () => {
  const calls = [];
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) =>
        opts && opts.trashed ? [] : [entryWith('e-1'), entryWith('e-2')],
      fetchEntryActions: async () => ({
        'e-1': [ACTION.trash],
        'e-2': [ACTION.trash],
      }),
      trashEntry: async (uuid) => {
        calls.push(uuid);
        if (uuid === 'e-1') throw new Error('refused');
        return null;
      },
    },
  });
  component.init();
  await component.load();
  component.toggleSelection('e-1');
  component.toggleSelection('e-2');
  await component.runBulkAction(ACTION.trash);
  assert.deepStrictEqual(Array.from(calls), ['e-1']);
  assert.match(component.error, /could not/i);
});

// --- dropping a tag or a folder from the browser ---------------------------

test('deleting a tag re-signs the entries carrying it, then removes it', async () => {
  const calls = [];
  const { component } = browser({
    api: {
      listTags: async () => [
        { uuid: 't-1', vault: VAULT_UUID, encrypted_name: 'ct', color: '#3b82f6', metadata_sig: 'sig' },
      ],
      listEntries: async (uuid, opts) =>
        opts && opts.trashed ? [] : [entryWith('e-1', { tags: ['t-1'] })],
      updateEntry: async (uuid) => { calls.push('put:' + uuid); return {}; },
      deleteTag: async (uuid) => { calls.push('delete:' + uuid); return null; },
    },
  });
  component.init();
  await component.load();
  component.confirm = async () => true;
  await component.deleteTag(component.tags[0]);
  assert.deepStrictEqual(Array.from(calls), ['put:e-1', 'delete:t-1']);
});

test('a refused confirmation deletes nothing and re-signs nothing', async () => {
  const calls = [];
  const { component } = browser({
    api: {
      listTags: async () => [
        { uuid: 't-1', vault: VAULT_UUID, encrypted_name: 'ct', color: '#3b82f6', metadata_sig: 'sig' },
      ],
      listEntries: async (uuid, opts) =>
        opts && opts.trashed ? [] : [entryWith('e-1', { tags: ['t-1'] })],
      updateEntry: async (uuid) => { calls.push('put:' + uuid); return {}; },
      deleteTag: async (uuid) => { calls.push('delete:' + uuid); return null; },
    },
  });
  component.init();
  await component.load();
  component.confirm = async () => false;
  await component.deleteTag(component.tags[0]);
  assert.deepStrictEqual(Array.from(calls), []);
});

test('a folder deletion carries its entries, trashed ones included', async () => {
  const bodies = [];
  const { component } = browser({
    api: {
      listFolders: async () => [
        { uuid: 'f-1', vault: VAULT_UUID, parent: null, position: 0,
          encrypted_name: 'ct', metadata_sig: 'sig' },
      ],
      listEntries: async (uuid, opts) =>
        opts && opts.trashed
          ? [entryWith('e-2', { folder: 'f-1', deleted_at: '2026-08-01' })]
          : [entryWith('e-1', { folder: 'f-1' })],
      deleteFolder: async (uuid, entries) => { bodies.push([uuid, entries]); return null; },
    },
  });
  component.init();
  await component.load();
  component.confirm = async () => true;
  await component.deleteFolder(component.folders[0]);
  assert.equal(bodies[0][0], 'f-1');
  assert.deepStrictEqual(Array.from(bodies[0][1], (item) => item.uuid).sort(), [
    'e-1',
    'e-2',
  ]);
});

test('a confirmation carries its own question rather than the default one', async () => {
  // AppDialog.confirm destructures its argument. Handed a string it leaves
  // every field on its default, and the user is asked "Are you sure?" about
  // an entry they are about to destroy.
  const asked = [];
  const { component, ctx } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [trashed('e-1')] : []),
      fetchEntryActions: async () => ({ 'e-1': [TRASH_ACTIONS.delete_forever] }),
      purgeEntry: async () => ({}),
    },
  });
  ctx.AppDialog = {
    confirm: async (options) => { asked.push(options); return true; },
  };
  component.init();
  await component.load();
  component.setView('trash');
  await component.runAction(TRASH_ACTIONS.delete_forever, component.entries[0]);
  assert.equal(asked.length, 1);
  assert.match(asked[0].message, /destroy this entry/i);
  assert.equal(asked[0].okClass, 'btn-error');
});

test('a dialog that says no stops the action', async () => {
  const purged = [];
  const { component, ctx } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [trashed('e-1')] : []),
      fetchEntryActions: async () => ({ 'e-1': [TRASH_ACTIONS.delete_forever] }),
      purgeEntry: async (uuid) => { purged.push(uuid); return {}; },
    },
  });
  ctx.AppDialog = { confirm: async () => false };
  component.init();
  await component.load();
  component.setView('trash');
  await component.runAction(TRASH_ACTIONS.delete_forever, component.entries[0]);
  assert.deepStrictEqual(Array.from(purged), []);
});

// --- a row that does not verify -------------------------------------------

test('an entry whose signature fails is counted and never rendered', async () => {
  const opens = [];
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) =>
        opts && opts.trashed ? [] : [entryWith('e-1'), entryWith('e-2')],
    },
    session: {
      verifyRecord: async (payload, sig) => {
        if (payload.entry_uuid === 'e-1') throw new Error('forged');
      },
    },
    crypto: {
      open: async (key, ciphertext, ad) => {
        opens.push(ad);
        return new TextEncoder().encode('open:' + ad);
      },
    },
  });
  component.init();
  await component.load();

  assert.deepStrictEqual(
    Array.from(component.visibleEntries(), (entry) => entry.uuid),
    ['e-2'],
  );
  assert.equal(component.tamperedCount, 1);
  // Not one field of the failed row was opened - not even its name, and
  // certainly not under a second associated-data string to see if that one
  // checks out.
  assert.ok(!opens.some((ad) => String(ad).startsWith('e-1|')));
});

test('a failed row is invisible to every listing, not just the current one', async () => {
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) =>
        opts && opts.trashed
          ? [Object.assign(entryWith('e-2'), { deleted_at: '2026-08-01' })]
          : [entryWith('e-1')],
    },
    session: { verifyRecord: async () => { throw new Error('forged'); } },
  });
  component.init();
  await component.load();
  for (const view of ['all', 'favorites', 'trash']) {
    component.setView(view);
    assert.deepStrictEqual(Array.from(component.visibleEntries()), [], view);
  }
  assert.equal(component.tamperedCount, 2);
});

test('a lock in the middle of a listing is not reported as tampering', async () => {
  // The two fail the same way, and the tamper message is the one the user is
  // told to act on rather than retry. An idle timeout must never wear it.
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
    },
    session: {
      verifyRecord: async () => {
        const error = new Error('locked');
        error.reason = 'locked';
        throw error;
      },
    },
  });
  component.init();
  await component.load();
  assert.equal(component.tamperedCount, 0);
  assert.equal(component.error, '');
});

test('a new folder is positioned among its siblings, not among all folders', async () => {
  // position orders siblings. Counting the whole tree would put every new
  // folder last in its own level and grow without meaning.
  const { component } = browser({
    api: {
      listFolders: async () => [
        { uuid: 'f-1', vault: VAULT_UUID, parent: null, position: 0,
          encrypted_name: 'ct', metadata_sig: 'sig' },
        { uuid: 'f-2', vault: VAULT_UUID, parent: 'f-1', position: 0,
          encrypted_name: 'ct', metadata_sig: 'sig' },
        { uuid: 'f-3', vault: VAULT_UUID, parent: 'f-1', position: 1,
          encrypted_name: 'ct', metadata_sig: 'sig' },
      ],
    },
  });
  component.init();
  await component.load();
  component.openFolder('f-1');
  component.newFolder();
  assert.equal(component.folderDraft.parent, 'f-1');
  assert.equal(component.folderDraft.position, 2);
});

test('a refresh shuts a menu that was still open', async () => {
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
      fetchEntryActions: async () => ({ 'e-1': [ACTION.edit] }),
    },
  });
  component.init();
  await component.load();
  await component.openMenu({ clientX: 0, clientY: 0 }, component.entries[0]);
  assert.equal(component.menu.open, true);
  await component.refresh();
  assert.equal(component.menu.open, false);
});

test('a field that will not open leaves the form shut and says so', async () => {
  // An unhandled rejection out of a click handler would leave no dialog and
  // no message - the user would press Edit and watch nothing happen.
  const { component } = typed({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
    },
    crypto: { open: async () => { throw new Error('cannot open'); } },
  });
  component.init();
  await component.load();
  await component.editEntry({
    uuid: 'e-1', type: 'login', fieldIds: ['username'], tags: [],
  });
  assert.strictEqual(component.draft, null);
  assert.match(component.error, /could not be opened/i);
});

test('renaming a folder updates it, and keeps where it sits in the tree', async () => {
  // The signature covers the parent and the position too, so a rename that
  // dropped them would move the folder to the root as a side effect.
  const written = [];
  const created = [];
  const { component } = browser({
    api: {
      listFolders: async () => [
        {
          uuid: 'f-1',
          vault: VAULT_UUID,
          parent: 'f-parent',
          position: 3,
          encrypted_name: 'ct',
          metadata_sig: 'sig',
        },
      ],
      updateFolder: async (uuid, body) => { written.push([uuid, body]); return body; },
      createFolder: async (body) => { created.push(body); return body; },
    },
  });
  component.init();
  await component.load();
  component.renameFolder(component.folders[0]);
  assert.equal(component.folderDraft.existing, true);
  component.folderDraft.name = 'Bills';
  await component.saveFolder();
  assert.equal(created.length, 0, 'a rename must not create a second folder');
  assert.equal(written.length, 1);
  assert.equal(written[0][0], 'f-1');
  assert.equal(written[0][1].parent, 'f-parent');
  assert.equal(written[0][1].position, 3);
});

test('a new folder is still created rather than updated', async () => {
  const written = [];
  const created = [];
  const { component } = browser({
    api: {
      updateFolder: async (uuid, body) => { written.push(body); return body; },
      createFolder: async (body) => { created.push(body); return body; },
    },
  });
  component.init();
  await component.load();
  component.newFolder();
  component.folderDraft.name = 'Travel';
  await component.saveFolder();
  assert.equal(written.length, 0);
  assert.equal(created.length, 1);
});

test('an action the client cannot carry out is never put in the menu', async () => {
  // The endpoint offers `move`, `set_tags` and `copy_totp`; nothing here can
  // run them yet. A row that does nothing when clicked is worse than one that
  // is not there.
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
      fetchEntryActions: async () => ({
        'e-1': [
          { id: 'edit', label: 'Edit', icon: 'pen', category: 'edit' },
          { id: 'move', label: 'Move to folder', icon: 'folder', category: 'organize', bulk: true },
          { id: 'set_tags', label: 'Edit tags', icon: 'tag', category: 'organize', bulk: true },
          { id: 'copy_totp', label: 'Copy code', icon: 'clock', category: 'clipboard' },
        ],
      }),
    },
  });
  component.init();
  await component.load();
  assert.deepStrictEqual(
    component.actionsFor(component.entries[0]).map((a) => a.id),
    ['edit']
  );
  component.selected = ['e-1'];
  assert.deepStrictEqual(component.bulkActions().map((a) => a.id), []);
});

test('favouriting an entry re-signs the record instead of asking for a flag', async () => {
  // is_favorite is inside the signed payload, so there is no endpoint that
  // flips it: the whole record is signed again and put back.
  const written = [];
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
      fetchEntryActions: async () => ({
        'e-1': [{ id: 'favorite', label: 'Add to favourites', icon: 'star', category: 'organize', bulk: true }],
      }),
      updateEntry: async (uuid, body) => { written.push([uuid, body]); return body; },
    },
  });
  component.init();
  await component.load();
  await component.runAction({ id: 'favorite' }, component.entries[0]);
  assert.equal(written.length, 1);
  assert.equal(written[0][0], 'e-1');
  assert.equal(written[0][1].is_favorite, true);
  assert.equal(written[0][1].metadata_sig, 'signature');
});

test('the bulk bar unfavourites every selected row', async () => {
  const written = [];
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) =>
        opts && opts.trashed
          ? []
          : [
              { ...entryWith('e-1'), is_favorite: true },
              { ...entryWith('e-2'), is_favorite: true },
            ],
      fetchEntryActions: async () => ({
        'e-1': [{ id: 'unfavorite', label: 'Remove', icon: 'star-off', category: 'organize', bulk: true }],
        'e-2': [{ id: 'unfavorite', label: 'Remove', icon: 'star-off', category: 'organize', bulk: true }],
      }),
      updateEntry: async (uuid, body) => { written.push([uuid, body.is_favorite]); return body; },
    },
  });
  component.init();
  await component.load();
  component.selected = ['e-1', 'e-2'];
  await component.runBulkAction({ id: 'unfavorite' });
  assert.deepStrictEqual(Array.from(written), [['e-1', false], ['e-2', false]]);
});

test('a tag is created with its name sealed and the record signed', async () => {
  // The section that lists tags was the only place one could be missing from,
  // and until now the only place they could not be made.
  const posted = [];
  const { component } = browser({
    api: { createTag: async (body) => { posted.push(body); return body; } },
  });
  component.init();
  await component.load();
  component.newTag();
  assert.equal(component.tagDraft.color, '#ef4444', 'a real colour, not "None"');
  component.tagDraft.name = '  Banking  ';
  await component.saveTag();
  assert.equal(posted.length, 1);
  assert.equal(posted[0].vault, VAULT_UUID);
  assert.equal(posted[0].color, '#ef4444');
  assert.equal(posted[0].metadata_sig, 'signature');
  assert.equal(posted[0].encrypted_name, 'b64');
  assert.equal(component.tagDraft, null);
});

test('a tag with no colour is written as neutral, not as an empty string', async () => {
  // The palette's "None" is '', which the column's vocabulary does not
  // include: the write would come back 400.
  const posted = [];
  const { component } = browser({
    api: { createTag: async (body) => { posted.push(body); return body; } },
  });
  component.init();
  await component.load();
  component.newTag();
  component.tagDraft.name = 'Plain';
  component.tagDraft.color = '';
  await component.saveTag();
  assert.equal(posted[0].color, 'neutral');
});

test('a nameless tag is never written', async () => {
  const posted = [];
  const { component } = browser({
    api: { createTag: async (body) => { posted.push(body); return body; } },
  });
  component.init();
  await component.load();
  component.newTag();
  component.tagDraft.name = '   ';
  await component.saveTag();
  assert.equal(posted.length, 0);
  assert.notEqual(component.tagDraft, null, 'the dialog stays open to be corrected');
});

// --- landing on /vault, with no uuid in the page ----------------------------
//
// The listing used to answer here. Without it the controller has to choose,
// and it can only choose after the unlock: before that every name is a
// ciphertext and no key exists.

const FAV_UUID = '01a051b9-0000-7000-8000-0000000000fa';

function favouriteRow() {
  return Object.assign(vaultRow(FAV_UUID, 'Work'), { is_favorite: true });
}

test('with no uuid the browser opens the vault last seen on this device', async () => {
  const { component, ctx } = browser({
    data: { 'vault-uuid': null },
    vaults: [favouriteRow(), vaultRow(VAULT_UUID, 'Personal')],
  });
  ctx.localStorage.setItem('vault.lastVault', VAULT_UUID);
  component.init();
  await component.load();
  assert.equal(component.openVault && component.openVault.uuid, VAULT_UUID);
  assert.equal(component.missing, false);
});

test('a remembered vault that no longer exists falls back to the favourite', async () => {
  const { component, ctx } = browser({
    data: { 'vault-uuid': null },
    vaults: [vaultRow(VAULT_UUID, 'Personal'), favouriteRow()],
  });
  ctx.localStorage.setItem('vault.lastVault', 'a-vault-deleted-elsewhere');
  component.init();
  await component.load();
  // Nothing is out of reach - the pointer was stale, which is not a failure
  // worth a banner.
  assert.equal(component.openVault && component.openVault.uuid, FAV_UUID);
  assert.equal(component.missing, false);
});

test('with nothing remembered and no favourite it opens the first vault', async () => {
  const { component } = browser({
    data: { 'vault-uuid': null },
    vaults: [vaultRow('v-9', 'Only'), vaultRow('v-10', 'Second')],
  });
  component.init();
  await component.load();
  assert.equal(component.openVault && component.openVault.uuid, 'v-9');
});

test('an account with no vault lands on the empty state, not on an error', async () => {
  const { component } = browser({ data: { 'vault-uuid': null }, vaults: [] });
  component.init();
  await component.load();
  assert.equal(component.openVault, null);
  assert.equal(component.missing, false, 'no vault is not the same as one out of reach');
  assert.equal(component.error, '');
});

test('a uuid in the page still wins over what the device remembers', async () => {
  const { component, ctx } = browser({
    vaults: [vaultRow(VAULT_UUID, 'Personal'), favouriteRow()],
  });
  ctx.localStorage.setItem('vault.lastVault', FAV_UUID);
  component.init();
  await component.load();
  assert.equal(component.openVault.uuid, VAULT_UUID);
});

test('opening a vault records it as the one to come back to', async () => {
  const { component, ctx } = browser();
  component.init();
  await component.load();
  assert.equal(ctx.localStorage.getItem('vault.lastVault'), VAULT_UUID);
});

test('the uuid reaches the address bar without a navigation', async () => {
  // replaceState, never a redirect: a page load would take the closure that
  // holds the keys with it, and the master password would be asked again.
  const { component, ctx } = browser({ data: { 'vault-uuid': null } });
  component.init();
  await component.load();
  assert.deepStrictEqual(Array.from(ctx.history.replaced), ['/vault/' + VAULT_UUID]);
});

test('a vault whose signature is refused is never the one landed on', async () => {
  const { component } = browser({
    data: { 'vault-uuid': null },
    vaults: [forgedVaultRow('v-bad'), vaultRow('v-ok', 'Readable')],
    session: {
      verifyVaultMetadata: async (payload, sig) => {
        if (sig === 'forged') throw new Error('bad signature');
      },
    },
  });
  component.init();
  await component.load();
  assert.equal(component.openVault && component.openVault.uuid, 'v-ok');
});

test('a uuid that names no reachable vault still reports it as missing', async () => {
  // The distinction the landing path must not blur: asked for by name and not
  // found is worth saying; arriving with no name at all is not.
  const { component } = browser({ vaults: [] });
  component.init();
  await component.load();
  assert.equal(component.openVault, null);
  assert.equal(component.missing, true);
});

// --- an account with nothing in it ------------------------------------------

test('with no vault the page offers to create one rather than reporting a failure', async () => {
  const { component } = browser({ data: { 'vault-uuid': null }, vaults: [] });
  component.init();
  await component.load();
  assert.equal(component.hasNoVault(), true);
});

test('a vault out of reach is not the same as having none', async () => {
  // Told apart because the sentences differ: one says somebody else holds it,
  // the other says there is nothing here yet.
  const { component } = browser({ vaults: [] });
  component.init();
  await component.load();
  assert.equal(component.hasNoVault(), false);
  assert.equal(component.missing, true);
});

test('a vault that opens is never mistaken for an empty account', async () => {
  const { component } = browser();
  component.init();
  await component.load();
  assert.equal(component.hasNoVault(), false);
});

test('losing the last vault empties the sidebar with the listing', async () => {
  // Caught by looking at the screen, not by a test: the empty state hid the
  // table while the sidebar went on offering the tags and the trash count of
  // the vault that had just been deleted.
  const { component, options } = browser({
    api: {
      listTags: async () => [
        { uuid: 't-1', vault: VAULT_UUID, name: 'ct:Infra', color: '#ef4444',
          metadata_sig: 'sig' },
      ],
    },
  });
  component.init();
  await component.load();
  assert.ok(component.tags.length > 0, 'the vault opened with a tag on screen');

  // The vault is deleted elsewhere, and the reload finds nothing left.
  options.vaults = [];
  component.vaultUuid = null;
  await component.load();
  assert.equal(component.hasNoVault(), true);
  assert.deepStrictEqual(Array.from(component.tags), []);
  assert.deepStrictEqual(Array.from(component.folders), []);
  assert.deepStrictEqual(Array.from(component.entries), []);
  assert.deepStrictEqual(Array.from(component.entryRows), []);
});

test('losing the last vault takes its name out of the address bar', async () => {
  const { component, ctx, options } = browser({ data: { 'vault-uuid': null } });
  component.init();
  await component.load();
  assert.deepStrictEqual(Array.from(ctx.history.replaced), ['/vault/' + VAULT_UUID]);

  options.vaults = [];
  component.vaultUuid = null;
  await component.load();
  assert.equal(ctx.history.replaced[ctx.history.replaced.length - 1], '/vault');
  // And the device stops pointing at a vault nobody can open.
  assert.equal(ctx.localStorage.getItem('vault.lastVault'), null);
});
