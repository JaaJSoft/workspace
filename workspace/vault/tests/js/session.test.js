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
  // The decoded recovery key and the opened vault key, tracked the same way:
  // a fixed non-zero fixture so a test can prove the session zeroed the one
  // reference it actually held, not just that a fresh all-zero array exists.
  const secretBytes = Uint8Array.from({ length: 32 }, (_, i) => i + 65);
  const vaultKeyRaw = Uint8Array.from({ length: 32 }, (_, i) => i + 129);
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
    crockfordDecode: () => secretBytes,
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
    hpkeRecipient: async () => { calls.push('hpkeRecipient'); return { open: async () => vaultKeyRaw }; },
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
  return {
    ctx, calls, amk, kexPriv, sigSeed, secretBytes, vaultKeyRaw, storage, session: ctx.VaultSession,
  };
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

test('a broken kdf derivation is refused with a defined reason and zeroes what was already derived', async () => {
  const h = harness({ VaultCrypto: { hkdf: async () => { throw new Error('boom'); } } });
  await assert.rejects(
    h.session.unlock({ password: 'pw', secretText: SECRET, remember: false }),
    (err) => err.reason === 'identity'
  );
  assert.ok(h.amk.every((byte) => byte === 0));
  assert.ok(h.secretBytes.every((byte) => byte === 0));
});

test('a malformed served signing key is refused with a defined reason and zeroes both unwrapped private keys', async () => {
  const h = harness({ VaultCrypto: { decodePublicKey: () => { throw new Error('bad algorithm byte'); } } });
  await assert.rejects(
    h.session.unlock({ password: 'pw', secretText: SECRET, remember: false }),
    (err) => err.reason === 'substituted-key'
  );
  assert.ok(h.kexPriv.every((byte) => byte === 0));
  assert.ok(h.sigSeed.every((byte) => byte === 0));
  // Never surfaced while the identity it names was never verified.
  assert.equal(h.session.accountUuid(), null);
});

test('a broken signer import is refused with a defined reason and zeroes both unwrapped private keys', async () => {
  const h = harness({ VaultCrypto: { importSigner: async () => { throw new Error('boom'); } } });
  await assert.rejects(
    h.session.unlock({ password: 'pw', secretText: SECRET, remember: false }),
    (err) => err.reason === 'substituted-key'
  );
  assert.ok(h.kexPriv.every((byte) => byte === 0));
  assert.ok(h.sigSeed.every((byte) => byte === 0));
});

test('a broken key-exchange import is refused with a defined reason and zeroes both unwrapped private keys', async () => {
  const h = harness({ VaultCrypto: { hpkeRecipient: async () => { throw new Error('boom'); } } });
  await assert.rejects(
    h.session.unlock({ password: 'pw', secretText: SECRET, remember: false }),
    (err) => err.reason === 'substituted-key'
  );
  assert.ok(h.kexPriv.every((byte) => byte === 0));
  assert.ok(h.sigSeed.every((byte) => byte === 0));
});

test('a second unlock that fails after importing keys leaves the live session exactly as it was', async () => {
  // Distinguishes which attempt's signer is actually in use: the default
  // importSigner/hpkeRecipient stubs are indistinguishable across calls, so
  // a bug that let the second (aborted) attempt clobber the live session
  // would otherwise pass unnoticed.
  let importCalls = 0;
  let hpkeCalls = 0;
  const h = harness({
    VaultCrypto: {
      importSigner: async () => {
        importCalls += 1;
        const id = importCalls;
        return { sign: async () => Uint8Array.from([id]) };
      },
      hpkeRecipient: async () => {
        hpkeCalls += 1;
        if (hpkeCalls === 1) return { open: async () => new Uint8Array(0) };
        throw new Error('boom');
      },
    },
  });

  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: false });
  assert.equal(h.session.isUnlocked(), true);
  const signatureBefore = await h.session.sign({ v: 1 });

  await assert.rejects(
    h.session.unlock({ password: 'pw', secretText: SECRET, remember: false }),
    (err) => err.reason === 'substituted-key'
  );

  // Still unlocked, through the original session - not a TypeError from a
  // nulled-out signer, and not a signature from the aborted attempt's key.
  assert.equal(h.session.isUnlocked(), true);
  const signatureAfter = await h.session.sign({ v: 1 });
  assert.equal(signatureAfter, signatureBefore);
});

test('a broken master-key derivation is refused with a defined reason and zeroes the decoded recovery key', async () => {
  const h = harness({ VaultCrypto: { deriveAmk: async () => { throw new Error('boom'); } } });
  await assert.rejects(
    h.session.unlock({ password: 'pw', secretText: SECRET, remember: false }),
    (err) => err.reason === 'identity'
  );
  assert.ok(h.secretBytes.every((byte) => byte === 0));
});

test('a mistyped recovery key is refused with its own reason, not identity', async () => {
  const h = harness({ VaultCrypto: { crockfordDecode: () => { throw new Error('invalid crockford input'); } } });
  await assert.rejects(
    h.session.unlock({ password: 'pw', secretText: SECRET, remember: false }),
    (err) => err.reason === 'recovery-key'
  );
});

test('a crash mapped to a defined reason keeps the original error as its cause', async () => {
  const original = new TypeError('kexPubPayload is not a function');
  const h = harness({ VaultCrypto: { hkdf: async () => { throw original; } } });
  await assert.rejects(
    h.session.unlock({ password: 'pw', secretText: SECRET, remember: false }),
    (err) => err.reason === 'identity' && err.cause === original
  );
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

test('the attestation is verified against the recomputed key, never the served one', async () => {
  // Byte-identical to what publicKeyFromSeed recomputes (so the equality
  // check still passes and unlock proceeds), but tagged so a swap of
  // sigPublicRaw for the untrusted served value is observable even though
  // the two are indistinguishable by content alone.
  const servedKey = Object.assign(Uint8Array.from([115, 105, 103, 112, 117, 98]), { fromServer: true });
  let verifyBytesArg = null;
  const h = harness({
    VaultCrypto: {
      decodePublicKey: () => servedKey,
      verifyBytes: async (pub) => { verifyBytesArg = pub; },
    },
  });
  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: false });
  assert.equal(verifyBytesArg.fromServer, undefined);
  assert.deepEqual(Array.from(verifyBytesArg), [115, 105, 103, 112, 117, 98]);
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

test('a storage failure while remembering the device does not leave the session half-open', async () => {
  const h = harness({
    globals: {
      localStorage: {
        getItem: () => null,
        setItem: () => { throw new Error('quota exceeded'); },
        removeItem: () => {},
      },
    },
  });
  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: true });
  assert.equal(h.session.isUnlocked(), true);
  let locked = 0;
  h.session.onLock(() => { locked += 1; });
  h.session.lock();
  assert.equal(locked, 1);
  assert.equal(h.session.isUnlocked(), false);
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

test('a broken metadata-key derivation still zeroes the opened vault key', async () => {
  const h = harness();
  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: false });
  h.ctx.VaultCrypto.hkdf = async () => { throw new Error('boom'); };
  await assert.rejects(h.session.openVaultKey('0192f3a4-2222-7d8e-9f01-23456789abcd', 'd3JhcHBlZA'));
  assert.ok(h.vaultKeyRaw.every((byte) => byte === 0));
});

test('pkcs8FromSeed refuses a seed shorter than 32 bytes', () => {
  const { ctx } = harness();
  assert.throws(() => ctx.pkcs8FromSeed(new Uint8Array(31)), /32/);
});

test('pkcs8FromSeed refuses a seed longer than 32 bytes', () => {
  const { ctx } = harness();
  assert.throws(() => ctx.pkcs8FromSeed(new Uint8Array(40)), /32/);
});

function clockHarness() {
  let current = 1000;
  const h = harness({
    globals: {
      Date: { now: () => current },
      addEventListener(name, handler) { (this.handlers ||= {})[name] = handler; },
    },
  });
  return { ...h, advance: (ms) => { current += ms; }, at: () => current };
}

test('a fresh unlock leaves five minutes on the clock', async () => {
  const h = clockHarness();
  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: false });
  assert.equal(h.session.secondsUntilLock(), 300);
});

test('the countdown runs down with the clock', async () => {
  const h = clockHarness();
  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: false });
  h.advance(120000);
  assert.equal(h.session.secondsUntilLock(), 180);
});

test('activity puts the five minutes back', async () => {
  const h = clockHarness();
  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: false });
  h.advance(240000);
  h.session.noteActivity();
  assert.equal(h.session.secondsUntilLock(), 300);
});

test('the session locks itself once the countdown reaches zero', async () => {
  const h = clockHarness();
  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: false });
  h.advance(300001);
  h.session.tick();
  assert.equal(h.session.isUnlocked(), false);
});

test('the countdown reads zero rather than a negative once locked', () => {
  // A session that never unlocked has expiresAt at its initial 0. Advancing
  // the clock below that (negative is fine - this Date is fully mocked, not
  // real wall-clock time) makes (expiresAt - Date.now()) positive, so the
  // Math.max(0, ...) clamp alone would no longer save a naive read: only the
  // `!unlocked` guard keeps this at zero.
  const h = clockHarness();
  h.advance(-2000);
  assert.equal(h.session.secondsUntilLock(), 0);
});

test('activity on a locked session does not resurrect the countdown', () => {
  const h = clockHarness();
  h.session.noteActivity();
  assert.equal(h.session.secondsUntilLock(), 0);
});

test('noteActivity never touches the clock while locked', () => {
  // secondsUntilLock() has its own `!unlocked` guard, so reading it back
  // cannot distinguish a noteActivity that wrote a stale expiresAt from one
  // that didn't - both read back as zero. Watching whether the guarded
  // statement ran at all (via a Date.now spy) isolates noteActivity's own
  // guard from that other one.
  let calls = 0;
  const h = harness({ globals: { Date: { now: () => { calls += 1; return 1000; } } } });
  h.session.noteActivity();
  assert.equal(calls, 0);
});

function watchHarness(overrides = {}) {
  const docListeners = {};
  const winListeners = {};
  const doc = {
    visibilityState: 'visible',
    addEventListener(name, handler) { (docListeners[name] ||= []).push(handler); },
  };
  const h = harness({
    ...overrides,
    globals: {
      document: doc,
      addEventListener(name, handler) { (winListeners[name] ||= []).push(handler); },
      ...overrides.globals,
    },
  });
  return { ...h, doc, docListeners, winListeners };
}

test('watchForIdle registers each listener once, even called twice', () => {
  const h = watchHarness();
  h.session.watchForIdle();
  h.session.watchForIdle();
  assert.equal(h.docListeners.pointerdown.length, 1);
  assert.equal(h.docListeners.keydown.length, 1);
  assert.equal(h.docListeners.wheel.length, 1);
  assert.equal(h.docListeners.visibilitychange.length, 1);
  assert.equal(h.winListeners.pagehide.length, 1);
});

test('a hidden tab locks the session', async () => {
  const h = watchHarness();
  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: false });
  h.session.watchForIdle();
  h.doc.visibilityState = 'hidden';
  h.docListeners.visibilitychange[0]();
  assert.equal(h.session.isUnlocked(), false);
});

test('accountKexPublicRaw is null before unlock', () => {
  const h = harness();
  assert.equal(h.session.accountKexPublicRaw(), null);
});

test('accountKexPublicRaw is set after unlock and cleared again after lock', async () => {
  const h = harness();
  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: false });
  assert.ok(h.session.accountKexPublicRaw() instanceof Uint8Array);
  h.session.lock();
  assert.equal(h.session.accountKexPublicRaw(), null);
});

test('pagehide locks the session', async () => {
  const h = watchHarness();
  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: false });
  h.session.watchForIdle();
  h.winListeners.pagehide[0]();
  assert.equal(h.session.isUnlocked(), false);
});

test('onTick subscribers fire on every tick, not just the one that locks', () => {
  const h = clockHarness();
  let calls = 0;
  h.session.onTick(() => { calls += 1; });
  h.session.tick();
  h.session.tick();
  assert.equal(calls, 2);
});

test('a tick reports the same remaining time the countdown itself would read', async () => {
  const h = clockHarness();
  await h.session.unlock({ password: 'pw', secretText: SECRET, remember: false });
  h.advance(60000);
  let seen = null;
  h.session.onTick(() => { seen = h.session.secondsUntilLock(); });
  h.session.tick();
  assert.equal(seen, 240);
});
