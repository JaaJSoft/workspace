// Rewriting a vault's metadata. Every field the request carries is inside the
// signed payload, so what is pinned here is that the request and the
// signature describe the same vault - a mismatch is a row the account can no
// longer verify, and only the client can repair it.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const VAULT = {
  uuid: 'v-1',
  name: 'Personnel',
  icon: 'lock',
  color: 'primary',
  is_favorite: false,
  key_version: 1,
  wrapped_key: 'AQ',
  encrypted_description: '',
};

function build(changes, overrides = {}) {
  const signed = [];
  const sealed = [];
  const ctx = loadScript('workspace/vault/ui/static/vault/ui/js/vault_update.js', {
    TextEncoder: globalThis.TextEncoder,
    vaultCrypto: {
      toBase64Url: () => 'sealed-name',
      seal: async (key, bytes) => {
        sealed.push(new TextDecoder().decode(bytes));
        return new Uint8Array(4);
      },
      KDF_HKDF_SHA256: 0x01,
      AD: { vaultFieldAd: (uuid, field) => `${uuid}|${field}` },
      vaultMetadataPayload: (fields) => fields,
      ...overrides.crypto,
    },
  });
  const session = {
    accountUuid: () => 'account-1',
    openVaultKey: async () => new Uint8Array(32),
    sign: async (payload) => {
      signed.push(payload);
      return 'signature';
    },
    ...overrides.session,
  };
  return ctx
    .buildVaultUpdateRequest(session, VAULT, changes)
    .then((body) => ({ body, signed, sealed }));
}

test('a rename seals the new name and signs it', async () => {
  const { body, signed, sealed } = await build({ name: 'Travail' });
  assert.deepStrictEqual(Array.from(sealed), ['Travail']);
  assert.equal(body.encrypted_name, 'sealed-name');
  assert.equal(signed[0].encrypted_name, 'sealed-name');
});

test('the signed payload describes the values the request carries', async () => {
  // The one failure this file exists to prevent: a body saying one thing and
  // a signature covering another leaves a row nothing can verify.
  const { body, signed } = await build({ icon: 'briefcase', color: 'info' });
  assert.equal(body.icon, signed[0].icon);
  assert.equal(body.color, signed[0].color);
  assert.equal(body.is_favorite, signed[0].is_favorite);
  assert.equal(body.encrypted_name, signed[0].encrypted_name);
});

test('an unchanged field is carried, not dropped', async () => {
  // There is no partial write of a signed record: omitting a field would
  // sign a payload the stored row does not match.
  const { body } = await build({ is_favorite: true });
  assert.equal(body.icon, 'lock');
  assert.equal(body.color, 'primary');
  assert.equal(body.is_favorite, true);
});

test('the payload names the vault owner, never the signer of the moment', async () => {
  // Frozen asymmetry with an entry, whose payload names its signer: in v2 a
  // member signs an entry they do not own, and never a vault.
  const { signed } = await build({ name: 'Travail' });
  assert.equal(signed[0].owner_account_uuid, 'account-1');
  assert.equal(signed[0].vault_uuid, 'v-1');
});

test('the name is sealed under the vault it belongs to', async () => {
  // Associated data is what stops a sealed name being moved to another
  // vault, so it has to name this one.
  let ad = null;
  await build(
    { name: 'Travail' },
    {
      crypto: {
        seal: async (key, bytes, associated) => {
          ad = associated;
          return new Uint8Array(4);
        },
      },
    },
  );
  assert.equal(ad, 'v-1|name');
});

test('changing nothing still produces a complete, signed request', async () => {
  const { body } = await build();
  assert.deepStrictEqual(Object.keys(body).sort(), [
    'color',
    'encrypted_description',
    'encrypted_name',
    'icon',
    'is_favorite',
    'metadata_sig',
  ]);
});
