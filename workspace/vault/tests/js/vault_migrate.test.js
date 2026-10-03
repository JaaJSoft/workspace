const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

function harness(overrides = {}) {
  const calls = [];
  const storage = new Map();
  const globals = {
    localStorage: {
      getItem: (k) => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => storage.set(k, String(v)),
    },
    vaultCrypto: {
      fromBase64Url: () => new Uint8Array([1]),
      mayResign: () => true,
    },
    vaultApi: {
      fetchMigration: async () => ({ vaults: [{ uuid: 'v', metadata: true, wrap: true, folders: [], tags: [], entries: ['e1'] }] }),
      listVaults: async () => [{ uuid: 'v', wrapped_key: 'w', hpke_suite: {}, metadata_sig: 'AQ', key_version: 1 }],
      listFolders: async () => [],
      listTags: async () => [],
      listEntries: async (vault, opts) => (opts && opts.trashed ? [] : [{ uuid: 'e1', metadata_sig: 'AQ' }, { uuid: 'e2', metadata_sig: 'AQ' }]),
      migrateVault: async (uuid, items) => { calls.push(['migrate', uuid, Array.from(items, (i) => i.kind + ':' + (i.uuid || ''))]); return null; },
    },
    vaultReader: {
      readVault: async (session, row) => ({ ...row, name: 'Vault' }),
      readFolders: async () => ({ rows: [], verifiedRows: [] }),
      readTags: async () => ({ rows: [], verifiedRows: [] }),
      readEntries: async (session, vault, rows) => ({ rows, verifiedRows: rows }),
    },
    buildEntryMigrateItem: async (s, v, row) => ({ kind: 'entry', uuid: row.uuid, fields: {}, encrypted_name: 'x' }),
    buildFolderMigrateItem: async (s, v, row) => ({ kind: 'folder', uuid: row.uuid, encrypted_name: 'x' }),
    buildTagMigrateItem: async (s, v, row) => ({ kind: 'tag', uuid: row.uuid, encrypted_name: 'x' }),
    buildVaultMetadataMigrateItem: async () => ({ kind: 'metadata', encrypted_name: 'x' }),
    ...overrides,
  };
  const session = { rewrapVaultKey: async () => ({ wrapped_key: 'n', hpke_suite: {} }), ...(overrides.session || {}) };
  const ctx = loadScript('workspace/vault/ui/static/vault/ui/js/vault_migrate.js', globals);
  return { ctx, calls, storage, session, globals };
}

test('migrates only the listed rows, wrap and metadata first', async () => {
  const { ctx, calls, session } = harness();
  const result = await ctx.vaultMigration.run(session);
  assert.equal(result.outcome, 'clean');
  assert.equal(result.wrote, true);
  assert.deepStrictEqual(calls, [['migrate', 'v', ['wrap:', 'metadata:', 'entry:e1']]]);
});

test('an empty listing sends nothing', async () => {
  const { ctx, calls, session } = harness({ vaultApi: { ...harness().globals.vaultApi, fetchMigration: async () => ({ vaults: [] }) } });
  const result = await ctx.vaultMigration.run(session);
  assert.deepStrictEqual(calls, []);
  assert.equal(result.wrote, false);
});

test('a row the reader rejected is never rewritten', async () => {
  const base = harness().globals;
  const { ctx, calls, session } = harness({
    vaultReader: { ...base.vaultReader, readEntries: async (s, v, rows) => ({ rows: [], verifiedRows: [] }) },
  });
  await ctx.vaultMigration.run(session);
  assert.deepStrictEqual(calls, [['migrate', 'v', ['wrap:', 'metadata:']]]);
});

test('a vault whose metadata did not verify is skipped entirely, wrap included', async () => {
  const base = harness().globals;
  const { ctx, calls, session } = harness({
    vaultReader: { ...base.vaultReader, readVault: async (s, row) => ({ ...row, tampered: true }) },
  });
  await ctx.vaultMigration.run(session);
  assert.deepStrictEqual(calls, []);
});

test('a signature that may not be re-signed is skipped', async () => {
  const base = harness().globals;
  const { ctx, calls, session } = harness({ vaultCrypto: { ...base.vaultCrypto, mayResign: () => false } });
  await ctx.vaultMigration.run(session);
  assert.deepStrictEqual(calls, [['migrate', 'v', ['wrap:']]]);
});

test('batches stop at 200 items', async () => {
  const base = harness().globals;
  const many = Array.from({ length: 450 }, (_, i) => 'e' + i);
  const sizes = [];
  const { ctx, session } = harness({
    vaultApi: {
      ...base.vaultApi,
      migrateVault: async (uuid, items) => { sizes.push(items.length); return null; },
      fetchMigration: async () => ({ vaults: [{ uuid: 'v', metadata: false, wrap: false, folders: [], tags: [], entries: many }] }),
      listEntries: async (v, o) => (o && o.trashed ? [] : many.map((uuid) => ({ uuid, metadata_sig: 'AQ' }))),
    },
  });
  await ctx.vaultMigration.run(session);
  assert.deepStrictEqual(sizes, [200, 200, 50]);
});

test('a 409 then an empty listing is a clean pass', async () => {
  const base = harness().globals;
  let listings = 0;
  const conflict = Object.assign(new Error('x'), { name: 'VaultApiError', status: 409 });
  const { ctx, session, storage } = harness({
    vaultApi: {
      ...base.vaultApi,
      fetchMigration: async () => (listings++ === 0 ? base.vaultApi.fetchMigration() : { vaults: [] }),
      migrateVault: async () => { throw conflict; },
    },
  });
  const result = await ctx.vaultMigration.run(session);
  assert.equal(result.outcome, 'clean');
  assert.equal(storage.get('vault.migration.failures'), '0');
});

test('a 400 abandons the vault and counts a failure', async () => {
  const base = harness().globals;
  const refused = Object.assign(new Error('x'), { name: 'VaultApiError', status: 400 });
  const { ctx, session, storage } = harness({ vaultApi: { ...base.vaultApi, migrateVault: async () => { throw refused; } } });
  const result = await ctx.vaultMigration.run(session);
  assert.equal(result.outcome, 'failed');
  assert.equal(storage.get('vault.migration.failures'), '1');
});

test('stops after a lock between batches', async () => {
  const base = harness().globals;
  const locked = Object.assign(new Error('locked'), { reason: 'locked' });
  const { ctx, session, storage, calls } = harness({
    buildEntryMigrateItem: async () => { throw locked; },
  });
  const result = await ctx.vaultMigration.run(session);
  assert.equal(result.outcome, 'locked');
  assert.deepStrictEqual(calls, []);
  assert.equal(storage.get('vault.migration.failures') ?? null, null);
});

test('warns after three failed passes in a row, never on storage errors', async () => {
  const base = harness().globals;
  const refused = Object.assign(new Error('x'), { name: 'VaultApiError', status: 503 });
  const { ctx, session, storage } = harness({ vaultApi: { ...base.vaultApi, migrateVault: async () => { throw refused; } } });
  storage.set('vault.migration.failures', '2');
  assert.equal((await ctx.vaultMigration.run(session)).warn, true);

  const broken = harness({
    localStorage: { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } },
    vaultApi: { ...base.vaultApi, migrateVault: async () => { throw refused; } },
  });
  assert.equal((await broken.ctx.vaultMigration.run(broken.session)).warn, false);
});

test('a lock while a batch is being sent stops the run and leaves the counter alone', async () => {
  const base = harness().globals;
  const locked = Object.assign(new Error('locked'), { reason: 'locked' });
  const { ctx, session, storage } = harness({ vaultApi: { ...base.vaultApi, migrateVault: async () => { throw locked; } } });
  storage.set('vault.migration.failures', '2');
  const result = await ctx.vaultMigration.run(session);
  assert.equal(result.outcome, 'locked');
  assert.equal(result.warn, false);
  assert.equal(storage.get('vault.migration.failures'), '2');
});

test('a clean pass resets the failure counter', async () => {
  const { ctx, session, storage } = harness();
  storage.set('vault.migration.failures', '2');
  await ctx.vaultMigration.run(session);
  assert.equal(storage.get('vault.migration.failures'), '0');
});

test('a second 409 abandons the vault without counting a failure', async () => {
  const base = harness().globals;
  const conflict = Object.assign(new Error('x'), { name: 'VaultApiError', status: 409 });
  const { ctx, session, storage } = harness({ vaultApi: { ...base.vaultApi, migrateVault: async () => { throw conflict; } } });
  const result = await ctx.vaultMigration.run(session);
  assert.equal(result.outcome, 'clean');
  assert.equal(storage.get('vault.migration.failures'), '0');
});

test('a 5xx stops the run before the next vault', async () => {
  const base = harness().globals;
  const down = Object.assign(new Error('x'), { name: 'VaultApiError', status: 503 });
  let sent = 0;
  const two = { uuid: 'v', metadata: false, wrap: true, folders: [], tags: [], entries: [] };
  const { ctx, session } = harness({
    vaultApi: {
      ...base.vaultApi,
      fetchMigration: async () => ({ vaults: [two, { ...two, uuid: 'w' }] }),
      listVaults: async () => [
        { uuid: 'v', wrapped_key: 'w', hpke_suite: {}, metadata_sig: 'AQ' },
        { uuid: 'w', wrapped_key: 'w', hpke_suite: {}, metadata_sig: 'AQ' },
      ],
      migrateVault: async () => { sent += 1; throw down; },
    },
  });
  const result = await ctx.vaultMigration.run(session);
  assert.equal(result.outcome, 'failed');
  assert.equal(sent, 1);
});
