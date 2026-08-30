// Sealing a brand-new vault. Every field the form offers is inside the signed
// payload, so what is pinned here is that the request and the signature
// describe the same vault: a mismatch is a row the account can never verify,
// and the server cannot repair one.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

function builder(overrides = {}) {
  const signed = [];
  const sealed = [];
  const ctx = loadScript('workspace/vault/ui/static/vault/ui/js/vault_create.js', {
    TextEncoder: globalThis.TextEncoder,
    TextDecoder: globalThis.TextDecoder,
    vaultCrypto: {
      uuidV7: () => 'minted-uuid',
      randomBytes: () => new Uint8Array(32),
      hkdf: async () => new Uint8Array(32),
      hpkeSeal: async () => new Uint8Array(64),
      toBase64Url: () => 'b64',
      // The associated data is recorded rather than the ciphertext: it is what
      // says which slot a value went into.
      seal: async (key, bytes, ad) => { sealed.push(ad); return new Uint8Array(4); },
      AD: {
        vaultFieldAd: (uuid, field) => `vault:${uuid}|${field}`,
        vaultKeyInfo: () => 'key-info',
        vaultMetaInfo: () => 'meta-info',
      },
      vaultMetadataPayload: (fields) => fields,
      KDF_HKDF_SHA256: 0x01,
      HPKE_SUITE_V1: { kem_id: 32, kdf_id: 1, aead_id: 2, mode: 0 },
      ...overrides.crypto,
    },
  });
  const session = {
    accountUuid: () => 'account-1',
    accountKexPublicRaw: () => new Uint8Array(32),
    sign: async (payload) => { signed.push(payload); return 'signature'; },
    ...overrides.session,
  };
  return { ctx, session, signed, sealed };
}

test('a new vault carries the icon and the colour it was given', async () => {
  const { ctx, session } = builder();
  const body = await ctx.buildVaultCreateRequest(session, {
    name: 'Work',
    description: 'Infrastructure accounts',
    icon: 'briefcase',
    color: 'info',
  }, 'v-1');
  assert.equal(body.icon, 'briefcase');
  assert.equal(body.color, 'info');
});

test('creation always signs is_favorite false, whatever the form asked', async () => {
  // The server sets it at creation and refuses a signature over anything
  // else. A vault created as a favourite is favourited by the update after.
  const { ctx, session, signed } = builder();
  await ctx.buildVaultCreateRequest(session, { name: 'Work', favorite: true }, 'v-1');
  assert.equal(signed[0].is_favorite, false);
});

test('the name and the description are sealed under their own slots', async () => {
  const { ctx, session, sealed } = builder();
  await ctx.buildVaultCreateRequest(session, {
    name: 'Work', description: 'Infrastructure accounts',
  }, 'v-1');
  assert.deepStrictEqual(Array.from(sealed), ['vault:v-1|name', 'vault:v-1|description']);
});

test('no description seals nothing for it', async () => {
  // The column takes an empty string. Sealing "" would store a ciphertext
  // that decrypts to nothing, and cost a slot to say it.
  const { ctx, session, sealed } = builder();
  const body = await ctx.buildVaultCreateRequest(session, { name: 'Work' }, 'v-1');
  assert.equal(body.encrypted_description, '');
  assert.deepStrictEqual(Array.from(sealed), ['vault:v-1|name']);
});

test('the signature covers every field the body carries', async () => {
  const { ctx, session, signed } = builder();
  const body = await ctx.buildVaultCreateRequest(session, {
    name: 'Work', description: 'x', icon: 'briefcase', color: 'info',
  }, 'v-1');
  const payload = signed[0];
  assert.equal(payload.vault_uuid, 'v-1');
  assert.equal(payload.owner_account_uuid, 'account-1');
  assert.equal(payload.encrypted_name, body.encrypted_name);
  assert.equal(payload.encrypted_description, body.encrypted_description);
  assert.equal(payload.icon, body.icon);
  assert.equal(payload.color, body.color);
  assert.equal(payload.key_version, 1);
});

test('an unnamed field falls back rather than writing undefined', async () => {
  const { ctx, session } = builder();
  const body = await ctx.buildVaultCreateRequest(session, { name: 'Work' }, 'v-1');
  assert.equal(body.icon, 'lock');
  assert.equal(body.color, 'primary');
});

test('the caller keeps the uuid, because a retry has to reuse it', async () => {
  // It is the key the server's conflict branch matches on: a retry after a
  // lost answer must describe the same vault, not a new one.
  const { ctx, session } = builder();
  const body = await ctx.buildVaultCreateRequest(session, { name: 'Work' }, 'given-uuid');
  assert.equal(body.uuid, 'given-uuid');
  const minted = await ctx.buildVaultCreateRequest(session, { name: 'Work' });
  assert.equal(minted.uuid, 'minted-uuid');
});
