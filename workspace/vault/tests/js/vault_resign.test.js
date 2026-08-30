// Dropping a tag or a folder without breaking a signature.
//
// The server cannot repair one: it would have to forge the account's. So the
// client re-signs every entry the removal touches *before* asking for it, and
// abandons the removal if any of those re-signatures fails. Getting the order
// wrong leaves rows whose signature covers a tag they no longer carry - and
// those rows read as tampered from then on, which is the loudest possible
// failure for a change nobody asked to be dangerous.
const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

const VAULT = { uuid: 'v-1', wrapped_key: 'AQ', key_version: 1 };

function row(uuid, overrides = {}) {
  return Object.assign(
    {
      uuid: uuid,
      vault: 'v-1',
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
      entry_fields: [{ field_id: 'password', encrypted_value: 'ct:password' }],
    },
    overrides,
  );
}

function resign(overrides = {}) {
  const calls = [];
  const opened = [];
  const signed = [];
  const ctx = loadScripts(
    [
      'workspace/vault/ui/static/vault/ui/js/entry_write.js',
      'workspace/vault/ui/static/vault/ui/js/vault_resign.js',
    ],
    {
      TextEncoder: globalThis.TextEncoder,
      TextDecoder: globalThis.TextDecoder,
      vaultCrypto: {
        toBase64Url: () => 'b64',
        fromBase64Url: (value) => value,
        seal: async () => { opened.push('seal'); return new Uint8Array(2); },
        open: async () => { opened.push('open'); return new Uint8Array(2); },
        AD: { entryFieldAd: (uuid, field) => uuid + '|' + field },
        entryMetadataPayload: (fields) => fields,
        KDF_HKDF_SHA256: 0x01,
      },
      vaultSession: {
        accountUuid: () => 'account-1',
        openEntryKey: async () => new Uint8Array(32),
        sign: async (payload) => { signed.push(payload); return 'fresh-signature'; },
        isUnlocked: () => true,
      },
      vaultApi: {
        updateEntry: async (uuid, body) => { calls.push('put:' + uuid); return body; },
        deleteTag: async (uuid) => { calls.push('delete-tag:' + uuid); return null; },
        deleteFolder: async (uuid, entries) => {
          calls.push('delete-folder:' + uuid + ':' + entries.length);
          return null;
        },
        ...overrides.api,
      },
    },
  );
  return { ctx, calls, opened, signed, session: ctx.vaultSession };
}

// --- re-signing without opening anything -----------------------------------

test('a re-signature keeps every ciphertext and opens none of them', async () => {
  // The signed payload covers the ciphertexts, not the plaintexts, so moving
  // a row between folders needs no key beyond the signing one. Opening a
  // password to change a tag would put a secret on screen for no reason.
  const { ctx, opened, session } = resign();
  const body = await ctx.buildEntryResignRequest(
    session, VAULT, row('e-1', { tags: ['t-1', 't-2'] }), { tags: ['t-2'] },
  );
  assert.deepStrictEqual(Array.from(opened), []);
  assert.equal(body.encrypted_name, 'ct:name');
  assert.deepStrictEqual({ ...body.fields }, { password: 'ct:password' });
  assert.deepStrictEqual(Array.from(body.tags), ['t-2']);
  assert.equal(body.metadata_sig, 'fresh-signature');
});

test('a re-signature leaves untouched fields exactly as they were', async () => {
  const { ctx, session } = resign();
  const original = row('e-1', { folder: 'f-1', is_favorite: true, tags: ['t-1'] });
  const body = await ctx.buildEntryResignRequest(session, VAULT, original, {});
  assert.equal(body.folder, 'f-1');
  assert.equal(body.is_favorite, true);
  assert.deepStrictEqual(Array.from(body.tags), ['t-1']);
});

test('the re-signed payload covers everything the row stores', async () => {
  // A payload that skipped the ciphertexts would verify on the server and
  // leave the row signed over less than it holds - a hole that only shows up
  // the next time somebody swaps a field.
  const { ctx, signed, session } = resign();
  const original = row('e-1', { folder: 'f-1', tags: ['t-1', 't-2'] });
  const body = await ctx.buildEntryResignRequest(session, VAULT, original, {
    folder: null,
  });
  const payload = signed[0];
  assert.equal(payload.entry_uuid, 'e-1');
  assert.equal(payload.vault_uuid, 'v-1');
  assert.equal(payload.entry_type, 'login');
  assert.equal(payload.folder_uuid, null);
  assert.equal(payload.encrypted_name, 'ct:name');
  assert.equal(payload.encrypted_notes, '');
  assert.equal(payload.key_version, 1);
  assert.equal(payload.entry_version, 1);
  assert.equal(payload.is_favorite, false);
  assert.deepStrictEqual(Array.from(payload.tag_uuids), ['t-1', 't-2']);
  assert.deepStrictEqual({ ...payload.fields }, { password: 'ct:password' });
  assert.deepStrictEqual({ ...payload.fields }, { ...body.fields });
});

// --- dropping a tag --------------------------------------------------------

test('a tag is deleted only after every entry carrying it has been re-signed', async () => {
  const { ctx, calls } = resign();
  await ctx.vaultResign.deleteTagSafely(VAULT, 't-1', [
    row('e-1', { tags: ['t-1', 't-2'] }),
    row('e-2', { tags: ['t-1'] }),
  ]);
  assert.deepStrictEqual(Array.from(calls), [
    'put:e-1',
    'put:e-2',
    'delete-tag:t-1',
  ]);
});

test('a failed re-signature stops the deletion', async () => {
  // Half-done is the one outcome that must not happen: the tag would be gone
  // and e-2 would still be signed over a tag it no longer carries.
  const { ctx, calls } = resign({
    api: {
      updateEntry: async (uuid) => {
        if (uuid === 'e-2') throw new Error('refused');
        return {};
      },
    },
  });
  await assert.rejects(() =>
    ctx.vaultResign.deleteTagSafely(VAULT, 't-1', [
      row('e-1', { tags: ['t-1'] }),
      row('e-2', { tags: ['t-1'] }),
    ]),
  );
  assert.ok(!calls.includes('delete-tag:t-1'));
});

test('an entry that does not carry the tag is not rewritten', async () => {
  const { ctx, calls } = resign();
  await ctx.vaultResign.deleteTagSafely(VAULT, 't-1', [
    row('e-1', { tags: ['t-2'] }),
    row('e-2', { tags: ['t-1'] }),
  ]);
  assert.deepStrictEqual(Array.from(calls), ['put:e-2', 'delete-tag:t-1']);
});

test('a tag nothing carries is deleted with no rewrite at all', async () => {
  const { ctx, calls } = resign();
  await ctx.vaultResign.deleteTagSafely(VAULT, 't-1', [row('e-1', { tags: [] })]);
  assert.deepStrictEqual(Array.from(calls), ['delete-tag:t-1']);
});

// --- dropping a folder -----------------------------------------------------

test('a folder is deleted with every one of its entries, trash included', async () => {
  // The server compares the submitted set against the folder's real contents
  // and refuses a mismatch. deleted_at is a view; folder_id is still a
  // RESTRICT reference, so a trashed entry has to travel with the rest.
  const { ctx, calls } = resign();
  await ctx.vaultResign.deleteFolderSafely(VAULT, 'f-1', [], [
    row('e-1', { folder: 'f-1' }),
    row('e-2', { folder: 'f-1', deleted_at: '2026-08-01' }),
    row('e-3', { folder: null }),
  ]);
  assert.deepStrictEqual(Array.from(calls), ['delete-folder:f-1:2']);
});

test('the entries travel re-signed with no folder', async () => {
  const bodies = [];
  const { ctx } = resign({
    api: {
      deleteFolder: async (uuid, entries) => { bodies.push(entries); return null; },
    },
  });
  await ctx.vaultResign.deleteFolderSafely(VAULT, 'f-1', [], [
    row('e-1', { folder: 'f-1' }),
  ]);
  assert.deepStrictEqual(Array.from(bodies[0], (item) => item.uuid), ['e-1']);
  assert.equal(bodies[0][0].metadata_sig, 'fresh-signature');
  // The endpoint takes a uuid and a signature and nothing else: it reads the
  // rest from the row it already holds.
  assert.deepStrictEqual(Object.keys(bodies[0][0]).sort(), ['metadata_sig', 'uuid']);
});

test('a folder with children is emptied from the bottom up', async () => {
  // VaultFolder.parent is CASCADE, so the server refuses a folder that still
  // has subfolders rather than taking signatures it was never shown with it.
  const { ctx, calls } = resign();
  await ctx.vaultResign.deleteFolderSafely(
    VAULT,
    'f-1',
    [
      { uuid: 'f-1', parent: null },
      { uuid: 'f-2', parent: 'f-1' },
      { uuid: 'f-3', parent: 'f-2' },
    ],
    [row('e-1', { folder: 'f-3' })],
  );
  assert.deepStrictEqual(Array.from(calls), [
    'delete-folder:f-3:1',
    'delete-folder:f-2:0',
    'delete-folder:f-1:0',
  ]);
});

test('a refused deletion partway up stops the walk', async () => {
  const { ctx, calls } = resign({
    api: {
      deleteFolder: async (uuid) => {
        if (uuid === 'f-2') throw new Error('refused');
        return null;
      },
    },
  });
  await assert.rejects(() =>
    ctx.vaultResign.deleteFolderSafely(
      VAULT,
      'f-1',
      [
        { uuid: 'f-1', parent: null },
        { uuid: 'f-2', parent: 'f-1' },
      ],
      [],
    ),
  );
  assert.ok(!calls.includes('delete-folder:f-1:0'));
});

test('a re-signature signs with the session it was handed, not the global one', async () => {
  // Onboarding builds a session of its own before window.vaultSession exists.
  // A builder that reached for the global would sign as nobody there, and the
  // row would come back tampered to the account that wrote it.
  const { ctx } = resign();
  const seen = [];
  const other = {
    accountUuid: () => 'account-2',
    sign: async (payload) => { seen.push(payload); return 'other-signature'; },
  };
  const body = await ctx.buildEntryResignRequest(other, VAULT, row('e-1'), {});
  assert.equal(body.metadata_sig, 'other-signature');
  assert.equal(seen.length, 1);
  assert.equal(seen[0].signer_account_uuid, 'account-2');
});
