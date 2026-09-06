const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const SCRIPT = 'workspace/vault/ui/static/vault/ui/js/vault_export_tree.js';

// One vault, one folder, one tag, two entries - one of them trashed.
function fixtures() {
  return {
    vaults: [{ uuid: 'v1', wrapped_key: 'w', encrypted_name: 'n' }],
    folders: [{ uuid: 'f1', vault: 'v1', parent: null, position: 0 }],
    tags: [{ uuid: 't1', vault: 'v1', color: 'red' }],
    live: [{
      uuid: 'e1', vault: 'v1', type: 'login', folder: 'f1', tags: ['t1'],
      is_favorite: true, deleted_at: null,
      encrypted_name: 'N', encrypted_notes: 'O',
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T00:00:00Z',
      last_used_at: null,
      entry_fields: [
        { field_id: 'username', encrypted_value: 'U' },
        { field_id: 'password', encrypted_value: 'P' },
      ],
    }],
    trashed: [{
      uuid: 'e2', vault: 'v1', type: 'login', folder: null, tags: [],
      is_favorite: false, deleted_at: '2026-02-01T00:00:00Z',
      encrypted_name: 'N2', encrypted_notes: '',
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T00:00:00Z',
      last_used_at: null, entry_fields: [],
    }],
  };
}

// Every ciphertext in the fixtures is its own plaintext, uppercased: the test
// is about the shape of the tree and the refusals, not about AES.
function load(overrides = {}) {
  const f = fixtures();
  const readerCounts = overrides.readerCounts || { entries: 0, folders: 0, tags: 0 };
  return loadScript(SCRIPT, {
    TextEncoder: globalThis.TextEncoder,
    TextDecoder: globalThis.TextDecoder,
    vaultApi: {
      listVaults: async () => f.vaults,
      listFolders: async () => f.folders,
      listTags: async () => f.tags,
      listEntries: async (uuid, options) => (options && options.trashed ? f.trashed : f.live),
    },
    vaultReader: {
      readVault: async (s, row) => Object.assign({}, row, { name: 'Perso', description: 'D' }),
      readEntries: async (s, v, rows) => {
        if (overrides.readerThrowsLocked) {
          const error = new Error('locked');
          error.reason = 'locked';
          throw error;
        }
        return { rows: rows, tamperedCount: readerCounts.entries };
      },
      readFolders: async (s, v, rows) => ({
        rows: rows.map((r) => Object.assign({}, r, { name: 'Banque' })),
        tamperedCount: readerCounts.folders,
      }),
      readTags: async (s, v, rows) => ({
        rows: rows.map((r) => Object.assign({}, r, { name: 'perso' })),
        tamperedCount: readerCounts.tags,
      }),
    },
    vaultCrypto: {
      fromBase64Url: (s) => s,
      AD: { entryFieldAd: (uuid, field) => `${uuid}|${field}` },
      open: async (key, ciphertext) => new TextEncoder().encode(String(ciphertext).toLowerCase()),
    },
  });
}

const session = { openEntryKey: async () => 'entry-key' };

test('the tree carries every vault, folder, tag and entry', async () => {
  const ctx = load();
  const tree = await ctx.vaultExportTree.buildTree(session, {});
  assert.equal(tree.format, 'vault-archive');
  assert.equal(tree.version, 1);
  assert.equal(tree.vaults.length, 1);
  const vault = tree.vaults[0];
  assert.equal(vault.name, 'Perso');
  // Objects built inside the vm carry that realm's prototypes, so they must be
  // normalized before deepStrictEqual's identity check against a host literal.
  assert.deepStrictEqual(vault.folders.map((f) => ({ ...f })), [{ id: 0, parent: null, name: 'Banque', position: 0 }]);
  assert.deepStrictEqual(vault.tags.map((t) => ({ ...t })), [{ id: 0, name: 'perso', color: 'red' }]);
  assert.equal(vault.entries.length, 2);
});

test('an entry references its folder and tags by local id, never by uuid', async () => {
  // A uuid is what binds a ciphertext to its place. Carrying the account's
  // uuids into a file meant to land elsewhere is what makes a backup a
  // permanent identifier for the account it came from.
  const ctx = load();
  const tree = await ctx.vaultExportTree.buildTree(session, {});
  const entry = tree.vaults[0].entries[0];
  assert.equal(entry.folder, 0);
  assert.deepStrictEqual(entry.tags, [0]);
  assert.ok(!JSON.stringify(tree).includes('e1'), 'an entry uuid reached the tree');
  assert.ok(!JSON.stringify(tree).includes('f1'), 'a folder uuid reached the tree');
  assert.ok(!JSON.stringify(tree).includes('v1'), 'a vault uuid reached the tree');
});

test('every field is opened, and the trash is kept and marked', async () => {
  const ctx = load();
  const tree = await ctx.vaultExportTree.buildTree(session, {});
  const [live, trashed] = tree.vaults[0].entries;
  assert.equal(live.name, 'n');
  assert.equal(live.notes, 'o');
  assert.deepStrictEqual({ ...live.fields }, { username: 'u', password: 'p' });
  assert.equal(live.trashed, false);
  // Kept, because a trashed entry is still restorable: a backup that drops it
  // loses data the user can still get back.
  assert.equal(trashed.trashed, true);
});

test('one unreadable row refuses the whole export', async () => {
  // A partial backup that looks complete is the worst failure a backup has:
  // it is only discovered at the moment it was needed.
  const ctx = load({ readerCounts: { entries: 1, folders: 0, tags: 0 } });
  await assert.rejects(
    () => ctx.vaultExportTree.buildTree(session, {}),
    (err) => err.reason === 'unreadable'
  );
});

test('a lock during the export aborts it, and is not reported as tampering', async () => {
  // vaultReader re-throws a locked error rather than counting it, precisely so
  // a closed vault is never confused with a forged one.
  const ctx = load({ readerThrowsLocked: true });
  await assert.rejects(
    () => ctx.vaultExportTree.buildTree(session, {}),
    (err) => err.reason === 'locked'
  );
});

test('an account with no vault refuses rather than producing an empty file', async () => {
  const ctx = loadScript(SCRIPT, {
    vaultApi: { listVaults: async () => [] },
    vaultReader: {}, vaultCrypto: {},
  });
  await assert.rejects(
    () => ctx.vaultExportTree.buildTree(session, {}),
    (err) => err.reason === 'empty'
  );
});
