const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const CURRENT = { algo: 'argon2id', params: { v: '1.3', m: 65536, t: 3, p: 2 } };

function harness(overrides = {}) {
  const calls = [];
  const states = { format: { 1: 'superseded', 2: 'current' }, aead: { 1: 'current' } };
  const vaultCrypto = {
    CURRENT_SUITE: { kdf: CURRENT },
    KDF_DIRECT: 0,
    AD: {
      unwrapInfo: () => 'unwrap',
      kexPrivAd: (uuid) => `kex:${uuid}`,
      sigPrivAd: (uuid) => `sig:${uuid}`,
    },
    isCurrent: (axis, id) => states[axis][id] === 'current',
    fromBase64Url: (text) => Uint8Array.from(text, (c) => c.charCodeAt(0)),
    toBase64Url: (bytes) => String.fromCharCode(...bytes),
    // The first character of a fixture names its format.
    decodeCiphertext: (raw) => ({ formatVersion: raw[0] - 48, aeadId: 1 }),
    equalBytes: (a, b) => a.length === b.length && a.every((v, i) => v === b[i]),
    assertAccountKdf: () => {},
    deriveAmk: async (opts) => { calls.push(['deriveAmk', opts.params]); return Uint8Array.from([7, 7]); },
    hkdf: async () => Uint8Array.from([9, 9]),
    open: async (key, raw, ad) => { calls.push(['open', ad]); return Uint8Array.from(raw.slice(1)); },
    seal: async (key, plaintext, ad, opts) => {
      calls.push(['seal', ad, opts]);
      return Uint8Array.from([50, ...plaintext]);
    },
    ...(overrides.vaultCrypto || {}),
  };
  const ctx = loadScript('workspace/vault/ui/static/vault/ui/js/account_envelope.js', { vaultCrypto });
  return { E: ctx.vaultEnvelope, calls };
}

const fields = (over = {}) => ({
  kdf_algo: 'argon2id',
  kdf_params: { v: '1.3', m: 65536, t: 3, p: 2 },
  wrapped_kex_priv: '2kex',
  wrapped_sig_priv: '2sig',
  ...over,
});

test('an envelope under the current suite is not stale', () => {
  const { E } = harness();
  assert.equal(E.isStale(fields()), false);
  assert.equal(E.needsDerivation(fields()), false);
});

test('a superseded format on either key makes it stale without a new derivation', () => {
  const { E } = harness();
  for (const over of [{ wrapped_kex_priv: '1kex' }, { wrapped_sig_priv: '1sig' }]) {
    assert.equal(E.isStale(fields(over)), true);
    assert.equal(E.needsDerivation(fields(over)), false);
  }
});

test('other params or another kdf need a new derivation', () => {
  const { E } = harness();
  for (const over of [
    { kdf_params: { v: '1.3', m: 131072, t: 3, p: 2 } },
    { kdf_params: { v: '1.3', m: 65536, t: 3, p: 2, extra: 1 } },
    { kdf_params: { v: '1.3', m: 65536, t: 3 } },
    { kdf_algo: 'scrypt' },
  ]) {
    assert.equal(E.needsDerivation(fields(over)), true, JSON.stringify(over));
    assert.equal(E.isStale(fields(over)), true);
  }
});

test('previousOf reads the four previous columns, or nothing', () => {
  const { E } = harness();
  assert.equal(E.previousOf({ ...fields(), previous_wrapped_kex_priv: null }), null);
  const previous = E.previousOf({
    ...fields(),
    previous_kdf_algo: 'argon2id',
    previous_kdf_params: { v: '1.3', m: 1, t: 1, p: 1 },
    previous_wrapped_kex_priv: '1pk',
    previous_wrapped_sig_priv: '1ps',
  });
  assert.equal(previous.wrapped_kex_priv, '1pk');
  assert.equal(previous.kdf_params.m, 1);
});

test('sealing rereads both keys and seals with the direct kdf at version zero', async () => {
  const { E, calls } = harness();
  const keys = { kexPriv: Uint8Array.from([1, 2]), sigSeed: Uint8Array.from([3, 4]) };
  const sealed = await E.sealAndReread(Uint8Array.from([9]), keys, 'acct');
  assert.equal(typeof sealed.wrapped_kex_priv, 'string');
  const seals = calls.filter((c) => c[0] === 'seal');
  assert.equal(seals.length, 2);
  for (const [, , opts] of seals) assert.deepStrictEqual({ ...opts }, { keyVersion: 0, kdfId: 0 });
  assert.deepStrictEqual(calls.filter((c) => c[0] === 'open').map((c) => c[1]), ['kex:acct', 'sig:acct']);
});

test('a reread that does not give back the same bytes refuses to hand anything over', async () => {
  const { E } = harness({ vaultCrypto: { open: async () => Uint8Array.from([0, 0]) } });
  const keys = { kexPriv: Uint8Array.from([1, 2]), sigSeed: Uint8Array.from([3, 4]) };
  await assert.rejects(E.sealAndReread(Uint8Array.from([9]), keys, 'acct'), (err) => err.name === 'VaultEnvelopeMismatch');
});

test('the amk is zeroed once the unwrap key exists', async () => {
  let amk;
  const { E } = harness({ vaultCrypto: { deriveAmk: async () => { amk = Uint8Array.from([5, 5]); return amk; } } });
  await E.deriveUnwrapKey(fields(), 'pw', new Uint8Array(32), new Uint8Array(32));
  assert.deepStrictEqual(Array.from(amk), [0, 0]);
});

test('a failure on the signing key zeroes the key exchange key already opened', async () => {
  let kex;
  const { E } = harness({
    vaultCrypto: {
      open: async (key, raw, ad) => {
        if (ad.startsWith('sig')) throw new Error('tag mismatch');
        kex = Uint8Array.from([4, 4]);
        return kex;
      },
    },
  });
  await assert.rejects(E.openKeys(Uint8Array.from([9]), fields(), 'acct'), /tag mismatch/);
  assert.deepStrictEqual(Array.from(kex), [0, 0]);
});

test('the rotate body names the current kdf and the envelope it replaces', () => {
  const { E } = harness();
  const body = E.rotateBody({ wrapped_kex_priv: 'n', wrapped_sig_priv: 'm' }, 'old');
  assert.deepStrictEqual(JSON.parse(JSON.stringify(body)), {
    kdf_algo: 'argon2id', kdf_params: CURRENT.params,
    wrapped_kex_priv: 'n', wrapped_sig_priv: 'm', expected_wrapped_kex_priv: 'old',
  });
});

test('the same value under another type is another parameter', () => {
  const { E } = harness();
  assert.equal(E.needsDerivation(fields({ kdf_params: { v: '1.3', m: '65536', t: 3, p: 2 } })), true);
  const unit = { algo: 'argon2id', params: { v: '1.3', m: 65536, t: 3, p: 1 } };
  const { E: single } = harness({ vaultCrypto: { CURRENT_SUITE: { kdf: unit } } });
  assert.equal(single.needsDerivation(fields({ kdf_params: { v: '1.3', m: 65536, t: 3, p: 1 } })), false);
  assert.equal(single.needsDerivation(fields({ kdf_params: { v: '1.3', m: 65536, t: 3, p: true } })), true);
});

// --- opening the current envelope, then the previous one --------------------

const PREVIOUS_PARAMS = { v: '1.3', m: 19456, t: 2, p: 1 };
const withPrevious = (over = {}) => ({
  ...fields(),
  uuid: 'acct',
  previous_kdf_algo: 'argon2id',
  previous_kdf_params: PREVIOUS_PARAMS,
  previous_wrapped_kex_priv: '2pkx',
  previous_wrapped_sig_priv: '2psg',
  ...over,
});
const named = (name) => { const err = new Error(name); err.name = name; return err; };
// Only the previous envelope's keys open: the password typed is the older one.
const previousOnly = (fail = new Error('tag mismatch')) => ({
  open: async (key, raw) => {
    if (String.fromCharCode(...raw).startsWith('2p')) return Uint8Array.from(raw.slice(1));
    throw fail;
  },
});
const derivations = (calls) => calls.filter(([name]) => name === 'deriveAmk').map(([, params]) => params);

test('the current envelope is opened when it can be', async () => {
  const { E, calls } = harness();
  const result = await E.openWithFallback(withPrevious(), 'pw', new Uint8Array(1), new Uint8Array(1));
  assert.equal(result.opened, 'current');
  assert.deepStrictEqual(derivations(calls), [CURRENT.params]);
});

test('a current envelope that does not open falls back to the previous one, and zeroes its key', async () => {
  const keys = [];
  const { E, calls } = harness({
    vaultCrypto: { ...previousOnly(), hkdf: async () => { const k = Uint8Array.from([9, 9]); keys.push(k); return k; } },
  });
  const result = await E.openWithFallback(withPrevious(), 'pw', new Uint8Array(1), new Uint8Array(1));
  assert.equal(result.opened, 'previous');
  assert.deepStrictEqual(derivations(calls), [CURRENT.params, PREVIOUS_PARAMS]);
  assert.deepStrictEqual(Array.from(keys[0]), [0, 0], 'the current unwrap key outlived its failed open');
  assert.strictEqual(result.unwrapKey, keys[1]);
});

test('without a previous envelope a failed open is a wrong password', async () => {
  const { E } = harness({ vaultCrypto: previousOnly() });
  await assert.rejects(
    E.openWithFallback({ ...fields(), uuid: 'acct' }, 'pw', new Uint8Array(1), new Uint8Array(1)),
    { name: 'VaultEnvelopeWrongPassword' },
  );
});

test('an envelope neither password opens is a wrong password', async () => {
  const { E, calls } = harness({ vaultCrypto: { open: async () => { throw new Error('tag mismatch'); } } });
  await assert.rejects(
    E.openWithFallback(withPrevious(), 'pw', new Uint8Array(1), new Uint8Array(1)),
    { name: 'VaultEnvelopeWrongPassword' },
  );
  assert.equal(derivations(calls).length, 2);
});

test('an algorithm the current envelope names and this build cannot read never falls back', async () => {
  const { E, calls } = harness({ vaultCrypto: previousOnly(named('UnsupportedAlgorithmError')) });
  await assert.rejects(
    E.openWithFallback(withPrevious(), 'pw', new Uint8Array(1), new Uint8Array(1)),
    { name: 'UnsupportedAlgorithmError' },
  );
  assert.equal(derivations(calls).length, 1);
});

test('a derivation that broke on the current envelope never falls back', async () => {
  const { E, calls } = harness({
    vaultCrypto: { ...previousOnly(), assertAccountKdf: (algo, params) => { if (params.m === 65536) throw new Error('broken'); } },
  });
  await assert.rejects(
    E.openWithFallback(withPrevious(), 'pw', new Uint8Array(1), new Uint8Array(1)),
    { message: 'broken' },
  );
  assert.equal(derivations(calls).length, 0);
});

test('wrapped keys this build cannot read are refused before the derivation runs', async () => {
  const { E, calls } = harness({
    vaultCrypto: { decodeCiphertext: () => { throw named('UnsupportedAlgorithmError'); } },
  });
  await assert.rejects(
    E.openWithFallback(withPrevious(), 'pw', new Uint8Array(1), new Uint8Array(1)),
    { name: 'UnsupportedAlgorithmError' },
  );
  assert.equal(derivations(calls).length, 0);
});
