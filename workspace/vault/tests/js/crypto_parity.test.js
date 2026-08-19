// The browser bundle must reproduce, byte for byte, what the Python reference
// produced in crypto_vectors.json. A silent encoding divergence between the two
// only shows up as a user who can no longer open their vault, so it is caught
// here instead.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { loadScript } = require('../../../common/tests/js/loader');

const vm = require('node:vm');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const VECTORS_TEXT = fs.readFileSync(
  path.join(REPO_ROOT, 'workspace', 'vault', 'tests', 'crypto_vectors.json'), 'utf8'
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

// Parsed inside the vm, like the fuzz corpus: a value built in the test realm
// carries the test realm's constructors, and a bundled library that branches
// on one of them takes a different path there than it does on a page.
const VECTORS = vm.runInContext('JSON.parse(__vectorsText)', ctx);
const V = ctx.VaultCrypto;

// Cross-realm gotcha: arrays built inside the vm carry that realm's
// prototypes, so deepStrictEqual fails its prototype check against test-side
// literals. Compare through base64url strings, which are plain primitives.
const b64 = (bytes) => V.toBase64Url(bytes);

test('base64url round-trips without padding', () => {
  const bytes = Uint8Array.from([0xfb, 0xff]);
  assert.equal(b64(bytes), '-_8');
  assert.equal(b64(V.fromBase64Url('-_8')), '-_8');
});

test('the wire header matches the reference layout', () => {
  const raw = V.encodeCiphertext({
    aeadId: V.AEAD_AES_256_GCM,
    kdfId: V.KDF_HKDF_SHA256,
    keyVersion: 1,
    iv: Uint8Array.from({ length: 12 }, (_, i) => i),
    ciphertext: new TextEncoder().encode('ciphertext-and-tag'),
  });
  assert.equal(raw[0], 0x01);
  assert.equal(raw[1], V.AEAD_AES_256_GCM);
  assert.equal(raw[2], V.KDF_HKDF_SHA256);
  assert.equal(raw[3], 0x00);
  assert.equal(raw[4], 0x01);
  assert.equal(raw[5], 12);
});

test('an unknown format_version is rejected without parsing', () => {
  const raw = V.encodeCiphertext({
    aeadId: V.AEAD_AES_256_GCM,
    kdfId: V.KDF_HKDF_SHA256,
    keyVersion: 1,
    iv: new Uint8Array(12),
    ciphertext: new Uint8Array(4),
  });
  raw[0] = 0x02;
  assert.throws(() => V.decodeCiphertext(raw), /format_version/);
});

test('an iv_len inconsistent with the aead is rejected', () => {
  const raw = V.encodeCiphertext({
    aeadId: V.AEAD_AES_256_GCM,
    kdfId: V.KDF_HKDF_SHA256,
    keyVersion: 1,
    iv: new Uint8Array(12),
    ciphertext: new Uint8Array(4),
  });
  raw[5] = 24;
  assert.throws(() => V.decodeCiphertext(raw), /iv_len/);
});

test('the aead vectors decode to the header the reference wrote', () => {
  for (const vector of VECTORS.aead) {
    const decoded = V.decodeCiphertext(V.fromBase64Url(vector.expected_wire_b64));
    assert.equal(decoded.keyVersion, vector.key_version, vector.id);
    assert.equal(decoded.kdfId, vector.kdf_id, vector.id);
    assert.equal(decoded.iv.length, 12, vector.id);
  }
});

test('argon2id vectors replay exactly', async () => {
  for (const vector of VECTORS.argon2id) {
    const amk = await V.deriveAmk({
      password: vector.password,
      secretKey: V.fromBase64Url(vector.secret_key_b64),
      salt: V.fromBase64Url(vector.salt_b64),
      params: vector.params,
    });
    assert.equal(b64(amk), vector.expected_amk_b64, vector.id);
  }
});

test('hkdf vectors replay exactly', async () => {
  for (const vector of VECTORS.hkdf) {
    const out = await V.hkdf(
      V.fromBase64Url(vector.ikm_b64), new TextEncoder().encode(vector.info), 32
    );
    assert.equal(b64(out), vector.expected_b64, vector.id);
  }
});

test('aead vectors replay exactly and reopen', async () => {
  for (const vector of VECTORS.aead) {
    const ad = new TextEncoder().encode(vector.ad);
    const raw = await V.seal(
      V.fromBase64Url(vector.key_b64),
      new TextEncoder().encode(vector.plaintext),
      ad,
      {
        iv: V.fromBase64Url(vector.iv_b64),
        keyVersion: vector.key_version,
        kdfId: vector.kdf_id,
      }
    );
    assert.equal(b64(raw), vector.expected_wire_b64, vector.id);
    const plain = await V.open(V.fromBase64Url(vector.key_b64), raw, ad);
    assert.equal(new TextDecoder().decode(plain), vector.plaintext, vector.id);
  }
});

test('a substituted associated data fails to open', async () => {
  const vector = VECTORS.aead[0];
  await assert.rejects(
    () => V.open(
      V.fromBase64Url(vector.key_b64),
      V.fromBase64Url(vector.expected_wire_b64),
      new TextEncoder().encode(`${vector.ad}-tampered`)
    )
  );
});

test('cbor vectors replay exactly', () => {
  for (const vector of VECTORS.cbor) {
    assert.equal(b64(V.canonicalCbor(vector.payload)), vector.expected_b64, vector.id);
  }
});

test('canonical cbor is stable across key insertion order', () => {
  assert.equal(
    b64(V.canonicalCbor({ v: 1, type: 'test' })),
    b64(V.canonicalCbor({ type: 'test', v: 1 }))
  );
});

test('canonical cbor refuses a float', () => {
  // Two implementations round differently and the signature stops matching.
  assert.throws(() => V.canonicalCbor({ v: 1, t: 1.5 }), /float/i);
});

// HPKE draws an ephemeral key per seal and @hpke/core exposes no override for
// it, so two seals of the same plaintext differ and byte equality with the
// reference is unreachable. What has to hold is interoperability, which is what
// these two tests assert: the bundle opens what the reference sealed, and its
// own seals reopen. A divergence on the suite, on info or on aad breaks both.
test('the bundle opens what the reference implementation sealed', async () => {
  for (const vector of VECTORS.hpke) {
    const opened = await V.hpkeOpen(
      V.fromBase64Url(vector.recipient_sk_b64),
      new TextEncoder().encode(vector.info),
      V.fromBase64Url(vector.expected_sealed_b64)
    );
    assert.equal(b64(opened), vector.plaintext_b64, vector.id);
  }
});

test('a bundle seal reopens, and a different info does not', async () => {
  for (const vector of VECTORS.hpke) {
    const info = new TextEncoder().encode(vector.info);
    const sealed = await V.hpkeSeal(
      V.fromBase64Url(vector.recipient_pk_b64), info, V.fromBase64Url(vector.plaintext_b64)
    );
    const opened = await V.hpkeOpen(V.fromBase64Url(vector.recipient_sk_b64), info, sealed);
    assert.equal(b64(opened), vector.plaintext_b64, vector.id);
    // All context binding lives in info, so info alone stands between a wrap
    // for one vault and a wrap for another.
    await assert.rejects(() => V.hpkeOpen(
      V.fromBase64Url(vector.recipient_sk_b64), new TextEncoder().encode(`${vector.info}x`), sealed
    ), vector.id);
  }
});

test('ed25519 vectors replay exactly', async () => {
  for (const vector of VECTORS.ed25519) {
    // A vector carries either a CBOR payload or a raw message. The account key
    // attestation is the raw kind: routing it through sign() would wrap it in a
    // CBOR byte string and produce a signature no verifier accepts.
    const signature = vector.message_b64
      ? await V.signBytes(V.fromBase64Url(vector.sk_b64), V.fromBase64Url(vector.message_b64))
      : await V.sign(V.fromBase64Url(vector.sk_b64), vector.payload);
    assert.equal(b64(signature), vector.expected_sig_b64, vector.id);
    assert.equal(signature[0], V.SIG_ALG_ED25519, vector.id);
  }
});


test('verification rejects a replayed type before touching ed25519', async () => {
  const vector = VECTORS.ed25519[0];
  await assert.rejects(
    () => V.verify(
      V.fromBase64Url(vector.pk_b64),
      V.canonicalCbor(vector.payload),
      V.fromBase64Url(vector.expected_sig_b64),
      'share_record'
    ),
    /type/
  );
});

test('verification rejects a non-canonical encoding', async () => {
  const vector = VECTORS.ed25519[0];
  const canonical = V.canonicalCbor(vector.payload);
  // Indefinite-length map: decodable, but not the canonical form.
  const nonCanonical = Uint8Array.from([0xbf, ...canonical.slice(1), 0xff]);
  await assert.rejects(
    () => V.verify(
      V.fromBase64Url(vector.pk_b64),
      nonCanonical,
      V.fromBase64Url(vector.expected_sig_b64),
      vector.payload.type
    ),
    /canonical/
  );
});

test('a non-integer key version is refused rather than truncated', () => {
  // The header bytes are written with >> and &, which would turn 1.5 into 1
  // without a word. The reference implementation raises, so this one must too.
  assert.throws(
    () => V.encodeCiphertext({
      aeadId: V.AEAD_AES_256_GCM,
      kdfId: V.KDF_HKDF_SHA256,
      keyVersion: 1.5,
      iv: new Uint8Array(12),
      ciphertext: new Uint8Array(4),
    }),
    /key_version/
  );
});

test('a key given as a view into a larger buffer still seals and opens', async () => {
  // Uint8Array.buffer would hand the KEM the whole backing store, so the keys
  // are copied before deserialization. A subarray is how a caller slicing an
  // envelope would naturally pass them.
  const vector = VECTORS.hpke[0];
  const backing = new Uint8Array(96);
  backing.set(V.fromBase64Url(vector.recipient_pk_b64), 16);
  const view = backing.subarray(16, 48);
  const info = new TextEncoder().encode(vector.info);
  const sealed = await V.hpkeSeal(view, info, V.fromBase64Url(vector.plaintext_b64));
  const opened = await V.hpkeOpen(V.fromBase64Url(vector.recipient_sk_b64), info, sealed);
  assert.equal(b64(opened), vector.plaintext_b64);
});

test('a byte string encodes as the reference writes it, untagged', () => {
  // cbor-x wraps byte strings in tag 64 by default and canonical CBOR admits
  // no tags. Signed payloads carry raw identifiers and nonces, so this is the
  // shape that would have diverged first.
  assert.equal(
    Buffer.from(V.canonicalCbor({ v: 1, k: Uint8Array.from([1, 2, 3]) })).toString('hex'),
    'a2616b43010203617601'
  );
});

test('a type with no agreed encoding is refused', () => {
  for (const payload of [{ v: 1, d: new Date(0) }, { v: 1, s: new Set([1]) }, { v: 1, u: undefined }]) {
    assert.throws(() => V.canonicalCbor(payload), /unsupported type/, JSON.stringify(payload));
  }
});

test('a non-string map key is refused', () => {
  const map = new Map([[1, 'one']]);
  assert.throws(() => V.canonicalCbor({ v: 1, m: map }), /must be strings/);
});

test('associated data outside ASCII is refused', () => {
  // Field identifiers come from the user, and the reference encodes them with
  // a strict ASCII codec.
  assert.throws(
    () => V.AD.entryFieldAd('0192f3a4-5b6c-7d8e-9f01-23456789abcd', 'caf\u00e9'),
    /must be ASCII/
  );
});

test('an aead key of the wrong length is refused', async () => {
  await assert.rejects(
    () => V.seal(new Uint8Array(16), new Uint8Array(1), new Uint8Array(0), {
      iv: new Uint8Array(12), keyVersion: 1, kdfId: 1,
    }),
    /32-byte key/
  );
});

test('a fractional or oversized header id is refused', () => {
  for (const overrides of [{ kdfId: 1.5 }, { kdfId: 256 }, { aeadId: 1.5 }]) {
    assert.throws(() => V.encodeCiphertext({
      aeadId: V.AEAD_AES_256_GCM, kdfId: V.KDF_HKDF_SHA256, keyVersion: 1,
      iv: new Uint8Array(12), ciphertext: new Uint8Array(4), ...overrides,
    }), /does not fit in one byte/, JSON.stringify(overrides));
  }
});

test('a ciphertext truncated inside its iv is refused', () => {
  const raw = V.encodeCiphertext({
    aeadId: V.AEAD_AES_256_GCM, kdfId: V.KDF_HKDF_SHA256, keyVersion: 1,
    iv: new Uint8Array(12), ciphertext: new Uint8Array(4),
  });
  assert.throws(() => V.decodeCiphertext(raw.slice(0, 8)), /shorter than its declared iv/);
});
