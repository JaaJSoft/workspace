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
  const copied = [];
  const visited = [];
  const ctx = loadScripts(
    [
      'workspace/vault/ui/static/vault/ui/js/vault_unlock.js',
      'workspace/vault/ui/static/vault/ui/js/vault_store.js',
      'workspace/vault/ui/static/vault/ui/js/vault_reader.js',
      'workspace/vault/ui/static/vault/ui/js/entry_write.js',
      'workspace/vault/ui/static/vault/ui/js/folder_write.js',
      'workspace/vault/ui/static/vault/ui/js/clipboard.js',
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
        verifyRecord: async () => {},
        verifyVaultMetadata: async () => {},
        sign: async () => 'signature',
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
  return { component: ctx.vaultBrowser(), opened, entryKeys, locks, copied, visited, ctx };
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

// --- the listing, its selection and the bar the selection raises -----------

function entryWith(uuid, overrides = {}) {
  return Object.assign(entryRow(uuid), overrides);
}

const ACTION = {
  edit: { id: 'edit', label: 'Edit', icon: 'pencil', bulk: false, css_class: '' },
  move: { id: 'move', label: 'Move to folder', icon: 'folder', bulk: true, css_class: '' },
  trash: { id: 'trash', label: 'Move to trash', icon: 'trash-2', bulk: true, css_class: 'text-error' },
  favorite: { id: 'favorite', label: 'Add to favourites', icon: 'star', bulk: true, css_class: '' },
  unfavorite: { id: 'unfavorite', label: 'Remove from favourites', icon: 'star-off', bulk: true, css_class: '' },
  copy_password: { id: 'copy_password', label: 'Copy password', icon: 'key-round', bulk: false, css_class: '' },
};

function ids(actions) {
  return Array.from(actions, (action) => action.id);
}

test('the bulk bar offers only what every selected row offers', async () => {
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) =>
        opts && opts.trashed ? [] : [entryWith('e-1'), entryWith('e-2')],
      fetchEntryActions: async () => ({
        'e-1': [ACTION.edit, ACTION.move, ACTION.trash, ACTION.copy_password],
        // No trash on this one: a member whose wrap was revoked mid-listing
        // is the realistic case, and the bar must not offer what one row
        // would refuse.
        'e-2': [ACTION.edit, ACTION.move],
      }),
    },
  });
  component.init();
  await component.load();
  component.toggleSelection('e-1');
  component.toggleSelection('e-2');
  assert.deepStrictEqual(ids(component.bulkActions()), ['move']);
});

test('a bulk action the registry marks single-row is never offered in bulk', async () => {
  const { component } = browser({
    api: {
      listEntries: async (uuid, opts) => (opts && opts.trashed ? [] : [entryWith('e-1')]),
      fetchEntryActions: async () => ({ 'e-1': [ACTION.edit, ACTION.copy_password, ACTION.move] }),
    },
  });
  component.init();
  await component.load();
  component.toggleSelection('e-1');
  assert.deepStrictEqual(ids(component.bulkActions()), ['move']);
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
  const opened = [];
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
  // The last write wins, and it is the empty one.
  assert.equal(copied[copied.length - 1], '');
});
