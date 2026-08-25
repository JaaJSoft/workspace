// The vault metadata format, pinned against the frozen vectors: the associated
// data strings, the key the fields are sealed under, and the exact byte shape
// of the payload the account signs. A divergence here is a vault whose name
// nobody can read back.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { loadScript } = require('../../../common/tests/js/loader');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const VECTORS_TEXT = fs.readFileSync(
  path.join(REPO_ROOT, 'workspace', 'vault', 'tests', 'crypto_vectors.json'),
  'utf8'
);

const ctx = loadScript(
  'workspace/vault/ui/static/vault/ui/js/vendor/vault-crypto.js',
  {
    crypto: globalThis.crypto,
    TextEncoder: globalThis.TextEncoder,
    TextDecoder: globalThis.TextDecoder,
    btoa: globalThis.btoa,
    atob: globalThis.atob,
    __vectorsText: VECTORS_TEXT,
  }
);
const VECTORS = vm.runInContext('JSON.parse(__vectorsText)', ctx);
const V = ctx.VaultCrypto;
const vector = (kind, id) => VECTORS[kind].find((entry) => entry.id === id);
const text = (bytes) => new TextDecoder().decode(bytes);

test('the metadata key info matches the reference', () => {
  const frozen = vector('hkdf', 'vault-meta-key');
  const uuid = frozen.info.split('|')[2];
  assert.equal(text(V.AD.vaultMetaInfo(uuid)), frozen.info);
});

test('the metadata key derives to the frozen bytes', async () => {
  const frozen = vector('hkdf', 'vault-meta-key');
  const derived = await V.hkdf(
    V.fromBase64Url(frozen.ikm_b64),
    V.AD.vaultMetaInfo(frozen.info.split('|')[2])
  );
  assert.equal(V.toBase64Url(derived), frozen.expected_b64);
});

test('the field associated data matches the reference', () => {
  const frozen = vector('aead', 'vault-field-name');
  const uuid = frozen.ad.split('|')[2];
  assert.equal(text(V.AD.vaultFieldAd(uuid, 'name')), frozen.ad);
});

test('a field outside the catalogue is refused', () => {
  const uuid = vector('aead', 'vault-field-name').ad.split('|')[2];
  for (const field of ['password', 'custom:x', '', 'notes']) {
    assert.throws(() => V.AD.vaultFieldAd(uuid, field));
  }
});

test('the vault name seals to the frozen ciphertext', async () => {
  const frozen = vector('aead', 'vault-field-name');
  const uuid = frozen.ad.split('|')[2];
  const sealed = await V.seal(
    V.fromBase64Url(frozen.key_b64),
    new TextEncoder().encode(frozen.plaintext),
    V.AD.vaultFieldAd(uuid, 'name'),
    {
      iv: V.fromBase64Url(frozen.iv_b64),
      keyVersion: frozen.key_version,
      kdfId: frozen.kdf_id,
    }
  );
  assert.equal(V.toBase64Url(sealed), frozen.expected_wire_b64);
});

test('the payload encodes to the frozen canonical CBOR', () => {
  const frozen = vector('cbor', 'vault-metadata');
  // Built inside the vm: cbor-x branches on constructor identity, and a
  // test-realm object takes a different path than a page would.
  const payload = vm.runInContext(
    `VaultCrypto.vaultMetadataPayload(JSON.parse(${JSON.stringify(
      JSON.stringify(frozen.payload)
    )}))`,
    ctx
  );
  assert.equal(V.toBase64Url(V.canonicalCbor(payload)), frozen.expected_b64);
});

test('the frozen signature verifies against the frozen payload', async () => {
  const frozen = vector('ed25519', 'vault-metadata-signature');
  const bytes = V.fromBase64Url(vector('cbor', 'vault-metadata').expected_b64);
  await V.verify(
    V.fromBase64Url(frozen.pk_b64),
    bytes,
    V.fromBase64Url(frozen.expected_sig_b64),
    V.VAULT_METADATA_TYPE
  );
});

test('a payload signed as a vault is refused when read as another type', async () => {
  const frozen = vector('ed25519', 'vault-metadata-signature');
  const bytes = V.fromBase64Url(vector('cbor', 'vault-metadata').expected_b64);
  await assert.rejects(
    V.verify(
      V.fromBase64Url(frozen.pk_b64),
      bytes,
      V.fromBase64Url(frozen.expected_sig_b64),
      'entry-metadata'
    )
  );
});

test('uuidV7 produces a version 7, variant 10 identifier', () => {
  const value = V.uuidV7();
  assert.match(
    value,
    /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
  );
});

test('uuidV7 sorts by creation order', () => {
  const first = V.uuidV7();
  const later = V.uuidV7();
  assert.ok(first <= later);
});
