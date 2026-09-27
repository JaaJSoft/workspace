// The manifest reaches the browser inside the bundle, and every reader picks
// its algorithm from the bytes it is handed. These tests hold the bundle to
// both halves.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
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

function bundleWithTestAead() {
  const ctx = loadScript('workspace/vault/ui/static/vault/ui/js/vendor/vault-crypto.js', {
    crypto: globalThis.crypto, TextEncoder: globalThis.TextEncoder,
    TextDecoder: globalThis.TextDecoder, btoa: globalThis.btoa, atob: globalThis.atob,
  });
  vm.runInContext(
    fs.readFileSync(
      path.join(REPO_ROOT, 'workspace/vault/ui/static/vault/ui/js/test_suites/test_aead.js'), 'utf8'
    ),
    ctx,
  );
  return ctx.vaultCrypto;
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
  assert.throws(() => B.registerAead(0x33, impl), /not declared/);
  assert.throws(() => B.registerAead(0x01, { ...impl, ivLength: 12 }), /already registered/);
});

test('registerAead accepts the declared test id with a correct impl', () => {
  const B = freshBundle();
  const impl = { ivLength: 16, importKey: async () => ({}), seal: async () => new Uint8Array(),
    open: async () => new Uint8Array() };
  B.registerAead(0xf0, impl);
  assert.deepEqual(Array.from(B.implementedIds('aead')), [0x01, 0xf0]);
});

test('registerAead refuses an id that is not a genuine integer', () => {
  const B = freshBundle();
  const impl = { ivLength: 16, importKey: async () => ({}), seal: async () => new Uint8Array(),
    open: async () => new Uint8Array() };
  // '1' as a string, and 'constructor' aliasing the manifest's prototype chain.
  assert.throws(() => B.registerAead('1', impl), /integer/);
  assert.throws(() => B.registerAead('constructor', impl), /integer/);
  assert.throws(() => B.registerAead(1.5, impl), /integer/);
  assert.throws(() => B.registerAead(-1, impl), /integer/);
  assert.throws(() => B.registerAead(256, impl), /integer/);
});

test('registerAead refuses an impl whose ivLength disagrees with the manifest', () => {
  const B = freshBundle();
  const impl = { ivLength: 12, importKey: async () => ({}), seal: async () => new Uint8Array(),
    open: async () => new Uint8Array() };
  assert.throws(() => B.registerAead(0xf0, impl), /iv_length/);
});

test('a keyring hides its handles and cannot be constructed from outside', async () => {
  const ring = await V.importAeadKey(KEY);
  assert.equal(ring.handles, undefined);
  assert.throws(() => new ring.constructor(new Map()), /constructed only by importAeadKey/);
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

test('the registered test aead opens beside AES-GCM under one keyring', async () => {
  const T = bundleWithTestAead();
  assert.deepEqual(Array.from(T.implementedIds('aead')), [0x01, 0xf0]);
  const ring = await T.importAeadKey(KEY);
  const aesGcm = await T.seal(ring, new TextEncoder().encode('a'), T.AD.entryFieldAd(ENTRY, 'password'),
    { keyVersion: 1, kdfId: T.KDF_HKDF_SHA256 });
  assert.equal(new TextDecoder().decode(await T.open(ring, aesGcm, T.AD.entryFieldAd(ENTRY, 'password'))), 'a');
});

const ACCOUNT = '01890a5d-ac96-774b-bcce-b302099a8058';
const VAULT = '01890a5d-ac96-774b-bcce-b302099a8059';
const FORMAT_1_HPKE = { kem_id: 32, kdf_id: 1, aead_id: 2, mode: 0 };
const isUnsupported = (axis) => (err) => err.name === 'UnsupportedAlgorithmError' && err.axis === axis;

test('vaultKeyInfo names the suite in format 2 only', () => {
  const dec = (b) => new TextDecoder().decode(b);
  assert.equal(dec(V.AD.vaultKeyInfo(VAULT, ACCOUNT, FORMAT_1_HPKE)), `v1|vault-key|${VAULT}|${ACCOUNT}`);
  assert.equal(dec(V.AD.vaultKeyInfo(VAULT, ACCOUNT, V.CURRENT_SUITE.hpke)),
    `v2|vault-key|${VAULT}|${ACCOUNT}|0020-0001-0002`);
});

test('an unknown hpke suite is unsupported', async () => {
  const recipient = await V.hpkeRecipient(new Uint8Array(32).fill(7));
  for (const stored of [{ ...V.CURRENT_SUITE.hpke, format: 9 }, { ...V.CURRENT_SUITE.hpke, aead_id: 3 }]) {
    await assert.rejects(recipient.open(new Uint8Array(1), new Uint8Array(48), stored),
      (err) => err.name === 'UnsupportedAlgorithmError' && err.axis === 'hpke');
  }
});

test('a stored hpke suite spells each format one way only, for the opener and the info alike', async () => {
  const recipient = await V.hpkeRecipient(new Uint8Array(32).fill(7));
  const current = V.CURRENT_SUITE.hpke;
  for (const stored of [
    { ...FORMAT_1_HPKE, format: 1 },
    { ...current, format: '2' },
    { ...current, format: true },
    { ...current, format: 'constructor' },
    { ...current, kdf_id: true },
    { ...current, kem_id: '32' },
    { ...FORMAT_1_HPKE, kdf_id: true },
    { ...FORMAT_1_HPKE, extra: 1 },
    { kem_id: 32, kdf_id: 1, aead_id: 2 },
    null,
    undefined,
  ]) {
    const label = JSON.stringify(stored);
    await assert.rejects(recipient.open(new Uint8Array(1), new Uint8Array(48), stored),
      isUnsupported('hpke'), label);
    await assert.rejects(V.hpkeSeal(new Uint8Array(32).fill(9), new Uint8Array(1), new Uint8Array(1), stored),
      isUnsupported('hpke'), label);
    assert.throws(() => V.AD.vaultKeyInfo(VAULT, ACCOUNT, stored), isUnsupported('hpke'), label);
  }
});

test('the account kdf is checked before Argon2 runs', () => {
  const params = { v: '1.3', m: 65536, t: 3, p: 2 };
  V.assertAccountKdf('argon2id', params);
  V.assertAccountKdf('argon2id', { ...params, algo: 'argon2id' });
  for (const [algo, bad] of [['scrypt', params], ['argon2id', { ...params, m: 4194304 }],
    ['argon2id', { ...params, v: '1.0' }], ['argon2id', { ...params, p: 0 }]]) {
    assert.throws(() => V.assertAccountKdf(algo, bad), (err) => err.name === 'UnsupportedAlgorithmError');
  }
});

test('an account kdf named after a prototype key, or with malformed parameters, is unsupported', () => {
  const params = { v: '1.3', m: 65536, t: 3, p: 2 };
  const shapedLikeParams = Object.assign([], params);
  for (const [algo, bad] of [
    ['constructor', params], ['toString', params],
    ['argon2id', { ...params, t: true }], ['argon2id', { ...params, m: '65536' }],
    ['argon2id', null], ['argon2id', undefined], ['argon2id', shapedLikeParams],
  ]) {
    assert.throws(() => V.assertAccountKdf(algo, bad), isUnsupported('kdf'), `${algo} ${JSON.stringify(bad)}`);
  }
});

test('deriveAmk needs its parameters spelled out', async () => {
  await assert.rejects(
    V.deriveAmk({ password: 'x', secretKey: new Uint8Array(32), salt: new Uint8Array(32) }),
    /needs params/,
  );
});

test('an unknown signature prefix or payload version is unsupported', async () => {
  const seed = new Uint8Array(32).fill(3);
  const signature = await V.signBytes(seed, new Uint8Array([1]));
  signature[0] = 0x09;
  await assert.rejects(V.verifyBytes(new Uint8Array(32), new Uint8Array([1]), signature),
    (err) => err.name === 'UnsupportedAlgorithmError' && err.axis === 'signature');
  const payload = V.canonicalCbor({ v: 7, type: 'tag-metadata' });
  await assert.rejects(V.verify(new Uint8Array(32), payload, new Uint8Array(65), 'tag-metadata'),
    (err) => err.name === 'UnsupportedAlgorithmError' && err.axis === 'payload');
});

test('a payload version spelled as a string or a bool is unsupported', async () => {
  for (const v of ['1', true]) {
    const payload = V.canonicalCbor({ v, type: 'tag-metadata' });
    await assert.rejects(V.verify(new Uint8Array(32), payload, new Uint8Array(65), 'tag-metadata'),
      isUnsupported('payload'), String(v));
  }
});

test('signatures carry the current algorithm, and a wrong length is refused before Ed25519', async () => {
  const seed = new Uint8Array(32).fill(3);
  const signature = await V.signBytes(seed, new Uint8Array([1]));
  assert.equal(signature[0], V.CURRENT_SUITE.signatureAlg);
  const signer = await V.importSigner(seed);
  assert.equal((await signer.sign(new Uint8Array([1])))[0], V.CURRENT_SUITE.signatureAlg);
  await assert.rejects(V.verifyBytes(new Uint8Array(32), new Uint8Array([1]), signature.slice(0, -1)),
    /wrong length/);
});

test('a public key is refused for the other usage, and unknown prefixes are unsupported', () => {
  const sig = V.encodePublicKey(new Uint8Array(32), V.CURRENT_SUITE.sigPublicKeyAlg);
  assert.equal(V.decodePublicKey(sig, 'sig').length, 32);
  assert.throws(() => V.decodePublicKey(sig, 'kex'), (err) => err.name !== 'UnsupportedAlgorithmError');
  assert.throws(() => V.decodePublicKey(Uint8Array.from([7, ...new Uint8Array(32)]), 'kex'),
    (err) => err.name === 'UnsupportedAlgorithmError');
});

test('the removed constants are gone from the bundle', () => {
  for (const name of ['HPKE_SUITE_V1', 'ARGON2_PARAMS', 'ARCHIVE_ARGON2_BOUNDS', 'SIG_ALG_ED25519',
    'PUBKEY_ALG_X25519', 'PUBKEY_ALG_ED25519']) {
    assert.equal(Object.hasOwn(V, name), false, name);
  }
});
