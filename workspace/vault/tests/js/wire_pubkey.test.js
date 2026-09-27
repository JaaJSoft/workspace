// X25519 and Ed25519 public keys are both 32 raw bytes, so the one-byte
// algorithm label is the only thing that tells them apart once stored. These
// tests pin that the label exists, differs, and is read back rather than
// assumed.
const test = require('node:test');
const assert = require('node:assert');
const vm = require('node:vm');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript(
  'workspace/vault/ui/static/vault/ui/js/vendor/vault-crypto.js',
  {
    crypto: globalThis.crypto,
    TextEncoder: globalThis.TextEncoder,
    TextDecoder: globalThis.TextDecoder,
    btoa: globalThis.btoa,
    atob: globalThis.atob,
  }
);
const V = ctx.vaultCrypto;

// Built inside the vm: a typed array from the test realm carries that realm's
// prototypes, and the bundle is entitled to branch on them.
const bytes = (length, fill) =>
  vm.runInContext(`new Uint8Array(${length}).fill(${fill})`, ctx);

const KEX = V.CURRENT_SUITE.kexPublicKeyAlg;
const SIG = V.CURRENT_SUITE.sigPublicKeyAlg;

test('ed25519 and x25519 do not share an algorithm byte', () => {
  assert.equal(SIG, 0x02);
  assert.notEqual(SIG, KEX);
});

test('an ed25519 public key encodes under its own algorithm byte', () => {
  const stored = V.encodePublicKey(bytes(32, 7), SIG);
  assert.equal(stored[0], 0x02);
  assert.equal(stored.length, 33);
});

test('a stored ed25519 key decodes back to its raw bytes', () => {
  const raw = bytes(32, 7);
  const stored = V.encodePublicKey(raw, SIG);
  assert.equal(V.toBase64Url(V.decodePublicKey(stored, 'sig')), V.toBase64Url(raw));
});

test('a stored key is refused when read for the other usage', () => {
  assert.throws(() => V.decodePublicKey(V.encodePublicKey(bytes(32, 7), SIG), 'kex'), /not a kex key/);
  assert.throws(() => V.decodePublicKey(V.encodePublicKey(bytes(32, 7), KEX), 'sig'), /not a sig key/);
});

test('an ed25519 key of the wrong length is refused, not truncated', () => {
  assert.throws(() => V.encodePublicKey(bytes(31, 7), SIG), /wants 32/);
});

test('encoding needs an algorithm: there is no default to fall back on', () => {
  assert.throws(() => V.encodePublicKey(bytes(32, 7)), (err) => err.name === 'UnsupportedAlgorithmError');
});

test('an unknown algorithm byte is refused', () => {
  const stored = V.encodePublicKey(bytes(32, 7), SIG);
  stored[0] = 0x7f;
  assert.throws(() => V.decodePublicKey(stored, 'sig'), (err) => err.name === 'UnsupportedAlgorithmError');
});
