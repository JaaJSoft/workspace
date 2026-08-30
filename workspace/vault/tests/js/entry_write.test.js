// What a written entry carries, and what it signs.
//
// The signature covers the whole record, so this is where a mistake is
// expensive: a payload that omits a field the row stores is a row nobody can
// verify afterwards, including its own author.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const VAULT = { uuid: 'v-1', wrapped_key: 'AQ', key_version: 3 };

function builder(overrides = {}) {
  const signed = [];
  const ctx = loadScript('workspace/vault/ui/static/vault/ui/js/entry_write.js', {
    TextEncoder: globalThis.TextEncoder,
    TextDecoder: globalThis.TextDecoder,
    vaultCrypto: {
      toBase64Url: (value) => 'b64:' + new TextDecoder().decode(value),
      // The sealed bytes are the plaintext and the slot, so a test can read
      // which value went into which field without a real cipher.
      seal: async (key, plaintext, ad, options) =>
        new TextEncoder().encode(
          new TextDecoder().decode(plaintext) + '@' + ad + '@kv' + options.keyVersion,
        ),
      AD: { entryFieldAd: (uuid, field) => uuid + '|' + field },
      entryMetadataPayload: (fields) => fields,
      KDF_HKDF_SHA256: 0x01,
      ...overrides.crypto,
    },
  });
  const session = {
    accountUuid: () => 'account-1',
    openEntryKey: async () => new Uint8Array(32),
    sign: async (payload) => { signed.push(payload); return 'signature'; },
    ...overrides.session,
  };
  return { ctx, session, signed };
}

const DRAFT = {
  uuid: 'e-1',
  type: 'login',
  folder: null,
  tags: ['t-1'],
  favorite: true,
  name: 'GitHub',
  notes: '',
  values: { username: 'chevron-j', password: 'hunter2' },
};

test('every value is sealed under its own field slot', async () => {
  const { ctx, session } = builder();
  const body = await ctx.buildEntryWriteRequest(session, VAULT, DRAFT);
  assert.equal(body.encrypted_name, 'b64:GitHub@e-1|name@kv3');
  assert.equal(body.fields.username, 'b64:chevron-j@e-1|username@kv3');
  assert.equal(body.fields.password, 'b64:hunter2@e-1|password@kv3');
});

test('an empty value removes the field rather than sealing an empty string', async () => {
  // A row keeping an empty ciphertext would still be a row the action
  // endpoint offers to copy a password from.
  const { ctx, session } = builder();
  const body = await ctx.buildEntryWriteRequest(
    session,
    VAULT,
    Object.assign({}, DRAFT, { values: { username: 'jc', password: '' } }),
  );
  assert.deepStrictEqual(Object.keys(body.fields), ['username']);
});

test('the signed payload covers every field the body carries', async () => {
  const { ctx, session, signed } = builder();
  const body = await ctx.buildEntryWriteRequest(session, VAULT, DRAFT);
  const payload = signed[0];
  assert.equal(payload.entry_uuid, 'e-1');
  assert.equal(payload.vault_uuid, 'v-1');
  assert.equal(payload.signer_account_uuid, 'account-1');
  assert.equal(payload.entry_type, 'login');
  assert.equal(payload.folder_uuid, null);
  assert.equal(payload.encrypted_name, body.encrypted_name);
  assert.equal(payload.encrypted_notes, body.encrypted_notes);
  assert.equal(payload.is_favorite, true);
  assert.deepStrictEqual(Array.from(payload.tag_uuids), ['t-1']);
  assert.deepStrictEqual({ ...payload.fields }, { ...body.fields });
});

test('the key version signed is the one the fields were sealed under', async () => {
  // A row created after a rotation and signed under the column default would
  // name a generation it was never encrypted with.
  const { ctx, session, signed } = builder();
  await ctx.buildEntryWriteRequest(session, VAULT, DRAFT);
  assert.equal(signed[0].key_version, 3);
});

test('an edit keeps the row version it was read at', async () => {
  const { ctx, session, signed } = builder();
  await ctx.buildEntryWriteRequest(
    session,
    VAULT,
    Object.assign({}, DRAFT, { entryVersion: 4 }),
  );
  assert.equal(signed[0].entry_version, 4);
});

test('notes stay an empty string rather than becoming absent', async () => {
  // Unlike a field, encrypted_notes is a column: there is no "absent" for it
  // to be, and the serializer refuses a missing one.
  const { ctx, session } = builder();
  const body = await ctx.buildEntryWriteRequest(session, VAULT, DRAFT);
  assert.equal(body.encrypted_notes, '');
});

// --- notes, which no form here edits -----------------------------------------

test('a draft with no notes plaintext keeps the ciphertext it was handed', async () => {
  // The write is a full signed replacement over PUT, so a draft that dropped
  // the notes column would erase stored notes the first time the user renamed
  // an entry - silently, and with no way back.
  const { ctx, session } = builder();
  const body = await ctx.buildEntryWriteRequest(session, VAULT, {
    ...DRAFT,
    notes: '',
    encryptedNotes: 'ct:notes-as-stored',
  });
  assert.equal(body.encrypted_notes, 'ct:notes-as-stored');
});

test('typed notes are sealed and replace the carried ciphertext', async () => {
  const { ctx, session } = builder();
  const body = await ctx.buildEntryWriteRequest(session, VAULT, {
    ...DRAFT,
    notes: 'a recovery hint',
    encryptedNotes: 'ct:notes-as-stored',
  });
  assert.equal(body.encrypted_notes, 'b64:a recovery hint@e-1|notes@kv3');
});

test('an entry that never had notes writes an empty column', async () => {
  const { ctx, session } = builder();
  const body = await ctx.buildEntryWriteRequest(session, VAULT, { ...DRAFT, notes: '' });
  assert.equal(body.encrypted_notes, '');
});
