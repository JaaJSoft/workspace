// The manifest reaches the browser inside the bundle, and every reader picks
// its algorithm from the bytes it is handed. These tests hold the bundle to
// both halves.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { loadScript } = require('../../../common/tests/js/loader');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const MANIFEST = JSON.parse(
  fs.readFileSync(path.join(REPO_ROOT, 'workspace', 'vault', 'crypto_suites.json'), 'utf8')
);

function freshBundle() {
  return loadScript('workspace/vault/ui/static/vault/ui/js/vendor/vault-crypto.js', {
    crypto: globalThis.crypto,
    TextEncoder: globalThis.TextEncoder,
    TextDecoder: globalThis.TextDecoder,
    btoa: globalThis.btoa,
    atob: globalThis.atob,
  }).vaultCrypto;
}

const V = freshBundle();
const KEY = Uint8Array.from({ length: 32 }, (_, i) => i);
const ENTRY = '01890a5d-ac96-774b-bcce-b302099a8057';

test('the current suite is format 2 with AES-256-GCM', () => {
  assert.equal(V.CURRENT_SUITE.formatVersion, 2);
  assert.equal(V.CURRENT_SUITE.aeadId, 0x01);
  assert.equal(V.CURRENT_SUITE.hpke.format, 2);
  assert.equal(V.CURRENT_SUITE.kdf.algo, 'argon2id');
});

test('the bundle implements every readable id the manifest declares, and no other', () => {
  for (const axis of ['format', 'aead', 'pubkey', 'signature', 'payload']) {
    const declared = Object.entries(MANIFEST[axis])
      .filter(([, entry]) => entry.state !== 'test')
      .map(([id]) => Number(id))
      .sort((a, b) => a - b);
    assert.deepEqual(Array.from(V.implementedIds(axis)), declared, axis);
  }
});

test('a format 1 and a format 2 ciphertext open side by side', async () => {
  const ring = await V.importAeadKey(KEY);
  const written = await V.seal(ring, new TextEncoder().encode('secret'),
    V.AD.entryFieldAd(ENTRY, 'password'), { keyVersion: 1, kdfId: V.KDF_HKDF_SHA256 });
  assert.equal(written[0], 2);
  // Format 1, produced the way the reference produces it.
  const header = Uint8Array.from([1, 1, 1, 0, 1, 12]);
  const iv = new Uint8Array(12);
  const ct = new Uint8Array(await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv, additionalData: new TextEncoder().encode(`v1|entry-field|${ENTRY}|password`) },
    await crypto.subtle.importKey('raw', KEY, 'AES-GCM', false, ['encrypt']),
    new TextEncoder().encode('old'),
  ));
  const format1 = new Uint8Array([...header, ...iv, ...ct]);
  const ad = V.AD.entryFieldAd(ENTRY, 'password');
  assert.equal(new TextDecoder().decode(await V.open(ring, written, ad)), 'secret');
  assert.equal(new TextDecoder().decode(await V.open(ring, format1, ad)), 'old');
});

test('an unknown format or aead is unsupported before any decryption', async () => {
  const ring = await V.importAeadKey(KEY);
  for (const [index, value, axis] of [[0, 3, 'format'], [1, 7, 'aead']]) {
    const raw = await V.seal(ring, new Uint8Array([1]), V.AD.entryFieldAd(ENTRY, 'password'),
      { keyVersion: 1, kdfId: V.KDF_HKDF_SHA256 });
    raw[index] = value;
    await assert.rejects(V.open(ring, raw, V.AD.entryFieldAd(ENTRY, 'password')),
      (err) => err.name === 'UnsupportedAlgorithmError' && err.axis === axis);
  }
});

test('the test aead id is unsupported until something registers it', async () => {
  const raw = Uint8Array.from([2, 0xf0, 1, 0, 1, 16, ...new Uint8Array(16), 1, 2, 3]);
  await assert.rejects(V.open(await V.importAeadKey(KEY), raw, V.AD.entryFieldAd(ENTRY, 'password')),
    (err) => err.name === 'UnsupportedAlgorithmError' && err.axis === 'aead');
});

test('registerAead refuses an id the manifest does not declare, and a second registration', () => {
  const B = freshBundle();
  const impl = { ivLength: 16, importKey: async () => ({}), seal: async () => new Uint8Array(),
    open: async () => new Uint8Array() };
  assert.throws(() => B.registerAead(0x33, impl));
  assert.throws(() => B.registerAead(0x01, impl));
});

test('a format 2 header byte is authenticated', async () => {
  const ring = await V.importAeadKey(KEY);
  for (const index of [2, 3, 4]) {
    const raw = await V.seal(ring, new Uint8Array([1]), V.AD.entryFieldAd(ENTRY, 'password'),
      { keyVersion: 1, kdfId: V.KDF_HKDF_SHA256 });
    raw[index] ^= 0x01;
    await assert.rejects(V.open(ring, raw, V.AD.entryFieldAd(ENTRY, 'password')),
      (err) => err.name !== 'UnsupportedAlgorithmError');
  }
});
