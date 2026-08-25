// The unlock sequence, with the cryptography stubbed: what is under test is
// the order of the steps and what survives them. The real primitives are
// pinned by the vectors, and the real sequence by the browser walk in
// tests/e2e/test_unlock_browser.py.
const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

const ENVELOPE = {
  uuid: '0192f3a4-1111-7d8e-9f01-23456789abcd',
  kdf_algo: 'argon2id',
  kdf_params: { v: '1.3', m: 65536, t: 3, p: 2 },
  kdf_salt: 'c2FsdA',
  kex_public: 'AQtrZXg',
  // Must decode to exactly what publicKeyFromSeed recomputes below, minus
  // the algorithm prefix: otherwise every unlock in this file fails as a
  // substituted key and the tests prove the opposite of what they claim.
  sig_public: 'Xsigpub',
  wrapped_kex_priv: 'd3JhcHBlZC1rZXg',
  wrapped_sig_priv: 'd3JhcHBlZC1zaWc',
  sig_over_kex_pub: 'AXNpZ25hdHVyZQ',
  state: 'active',
};

function harness(overrides = {}) {
  const calls = [];
  const amk = Uint8Array.from([1, 2, 3, 4]);
  // 32 bytes each: kexPriv is an X25519 scalar and sigSeed an Ed25519 seed,
  // and sigSeed in particular reaches the real (unmocked) pkcs8FromSeed via
  // publicKeyFromSeed - a short fixture would trip its length check before
  // the stubbed crypto.subtle ever runs.
  const kexPriv = Uint8Array.from({ length: 32 }, (_, i) => i + 1);
  const sigSeed = Uint8Array.from({ length: 32 }, (_, i) => i + 33);
  const storage = new Map();

  const crypto = {
    // The recomputed signing public key. Equal to what the envelope carries
    // unless a test says otherwise.
    subtle: {
      importKey: async () => ({ handle: 'ed25519' }),
      exportKey: async () => ({ x: 'sigpub' }),
    },
  };

  const VaultCrypto = {
    AD: {
      unwrapInfo: () => 'unwrap-info',
      kexPrivAd: (uuid) => `kex:${uuid}`,
      sigPrivAd: (uuid) => `sig:${uuid}`,
      kexPubPayload: (uuid, pub) => `kexpub:${uuid}:${pub}`,
      vaultKeyInfo: (v, r) => `vaultkey:${v}:${r}`,
      vaultMetaInfo: (v) => `vaultmeta:${v}`,
    },
    crockfordDecode: () => new Uint8Array(32),
    fromBase64Url: (text) => Uint8Array.from(text, (c) => c.charCodeAt(0)),
    toBase64Url: (bytes) => String.fromCharCode(...bytes),
    equalBytes: (a, b) => String(a) === String(b),
    decodePublicKey: (bytes) => bytes.slice(1),
    deriveAmk: async () => { calls.push('deriveAmk'); return amk; },
    hkdf: async () => { calls.push('hkdf'); return Uint8Array.from([13, 14]); },
    open: async (key, raw, ad) => {
      calls.push(`open:${ad}`);
      // Lets a test fail only the second open (the signing key) while the
      // first (the key-exchange key) still succeeds and lands in kexPriv -
      // the partial-failure shape the wrong-password catch must still cover.
      if (overrides.failOpen && String(ad).startsWith(overrides.failOpen)) {
        throw new Error('tag mismatch');
      }
      if (String(ad).startsWith('kex')) return kexPriv;
      return sigSeed;
    },
    verifyBytes: async () => { calls.push('verifyBytes'); },
    importSigner: async () => { calls.push('importSigner'); return { sign: async () => new Uint8Array(1) }; },
    hpkeRecipient: async () => { calls.push('hpkeRecipient'); return { open: async () => new Uint8Array(32) }; },
    canonicalCbor: () => new Uint8Array(2),
    ...(overrides.VaultCrypto || {}),
  };

  const ctx = loadScripts(
    [
      'workspace/vault/ui/static/vault/ui/js/api.js',
      'workspace/vault/ui/static/vault/ui/js/session.js',
    ],
    {
      crypto,
      VaultCrypto,
      TextEncoder: globalThis.TextEncoder,
      getCSRFToken: () => 'csrf',
      localStorage: {
        getItem: (k) => (storage.has(k) ? storage.get(k) : null),
        setItem: (k, v) => storage.set(k, v),
        removeItem: (k) => storage.delete(k),
      },
      document: { addEventListener() {} },
      addEventListener() {},
      setInterval: () => 1,
      clearInterval: () => {},
      Date: globalThis.Date,
      fetch: overrides.fetch
        || (async () => ({ ok: true, status: 200, json: async () => ENVELOPE })),
      ...overrides.globals,
    }
  );
  return { ctx, calls, amk, kexPriv, sigSeed, storage, session: ctx.VaultSession };
}

const SECRET = 'A'.repeat(53);

test('a session starts locked', () => {
  assert.equal(harness().session.isUnlocked(), false);
});

test('unlocking derives, unwraps both private keys and reports success', async () => {
  const h = harness();
  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: false });
  assert.equal(h.session.isUnlocked(), true);
  assert.deepEqual(h.calls.slice(0, 4), [
    'deriveAmk', 'hkdf',
    'open:kex:0192f3a4-1111-7d8e-9f01-23456789abcd',
    'open:sig:0192f3a4-1111-7d8e-9f01-23456789abcd',
  ]);
});

test('the account master key is zeroed once the unwrap key exists', async () => {
  const h = harness();
  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: false });
  assert.ok(h.amk.every((byte) => byte === 0));
});

test('the private key buffers are zeroed once imported', async () => {
  const h = harness();
  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: false });
  assert.ok(h.kexPriv.every((byte) => byte === 0));
  assert.ok(h.sigSeed.every((byte) => byte === 0));
});

test('a wrong password fails locally, with no request beyond the envelope', async () => {
  let requests = 0;
  const h = harness({
    fetch: async () => { requests += 1; return { ok: true, status: 200, json: async () => ENVELOPE }; },
    VaultCrypto: { open: async () => { throw new Error('tag mismatch'); } },
  });
  await assert.rejects(
    h.session.unlock({ password: 'wrong', secretText: SECRET, remember: false }),
    (err) => err.reason === 'password'
  );
  assert.equal(requests, 1);
  assert.equal(h.session.isUnlocked(), false);
});

test('a failure on the signing key still zeroes the already-unwrapped key exchange key', async () => {
  const h = harness({ failOpen: 'sig' });
  await assert.rejects(
    h.session.unlock({ password: 'wrong', secretText: SECRET, remember: false }),
    (err) => err.reason === 'password'
  );
  assert.ok(h.kexPriv.every((byte) => byte === 0));
});

test('a substituted signing public key is caught before it is trusted', async () => {
  const h = harness({
    globals: {
      crypto: {
        subtle: {
          importKey: async () => ({}),
          // Not the key the envelope carries.
          exportKey: async () => ({ x: 'other' }),
        },
      },
    },
  });
  await assert.rejects(
    h.session.unlock({ password: 'pw', secretText: SECRET, remember: false }),
    (err) => err.reason === 'substituted-key'
  );
  assert.equal(h.calls.includes('verifyBytes'), false);
  assert.equal(h.session.isUnlocked(), false);
});

test('the attestation is verified after the key comparison, never before', async () => {
  const h = harness();
  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: false });
  assert.ok(h.calls.indexOf('verifyBytes') > h.calls.indexOf('open:sig:0192f3a4-1111-7d8e-9f01-23456789abcd'));
});

test('an account with no identity is told so, not told the password is wrong', async () => {
  const h = harness({ fetch: async () => ({ ok: false, status: 404 }) });
  await assert.rejects(
    h.session.unlock({ password: 'pw', secretText: SECRET, remember: false }),
    (err) => err.reason === 'identity'
  );
});

test('remembering the device stores the recovery key and nothing else', async () => {
  const h = harness();
  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: true });
  assert.deepEqual([...h.storage.keys()], ['vault.secret-key']);
  assert.equal(h.storage.get('vault.secret-key'), SECRET);
  assert.equal(h.session.rememberedSecret(), SECRET);
});

test('not remembering the device stores nothing', async () => {
  const h = harness();
  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: false });
  assert.equal(h.storage.size, 0);
});

test('forgetting the device clears the stored recovery key', async () => {
  const h = harness();
  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: true });
  h.session.forgetDevice();
  assert.equal(h.session.rememberedSecret(), null);
});

test('locking leaves nothing reachable on the module', async () => {
  const h = harness();
  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: false });
  h.session.lock();
  assert.equal(h.session.isUnlocked(), false);
  // Every key the session holds lives in the closure, not on the object: a
  // property holding one would be readable from the console after a lock.
  assert.ok(
    Object.values(h.session).every((value) => typeof value === 'function')
  );
});

test('locking runs the registered callbacks once', async () => {
  const h = harness();
  let locked = 0;
  h.session.onLock(() => { locked += 1; });
  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: false });
  h.session.lock();
  h.session.lock();
  assert.equal(locked, 1);
});

test('signing before unlocking is refused rather than silently empty', async () => {
  await assert.rejects(harness().session.sign({ v: 1 }));
});

test('pkcs8FromSeed refuses a seed shorter than 32 bytes', () => {
  const { ctx } = harness();
  assert.throws(() => ctx.pkcs8FromSeed(new Uint8Array(31)), /32/);
});

test('pkcs8FromSeed refuses a seed longer than 32 bytes', () => {
  const { ctx } = harness();
  assert.throws(() => ctx.pkcs8FromSeed(new Uint8Array(40)), /32/);
});
