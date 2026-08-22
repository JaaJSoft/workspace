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
const V = ctx.VaultCrypto;

// Built inside the vm: a typed array from the test realm carries that realm's
// prototypes, and the bundle is entitled to branch on them.
const bytes = (length, fill) =>
  vm.runInContext(`new Uint8Array(${length}).fill(${fill})`, ctx);

test('ed25519 and x25519 do not share an algorithm byte', () => {
  assert.equal(V.PUBKEY_ALG_ED25519, 0x02);
  assert.notEqual(V.PUBKEY_ALG_ED25519, V.PUBKEY_ALG_X25519);
});

test('an ed25519 public key encodes under its own algorithm byte', () => {
  const stored = V.encodePublicKey(bytes(32, 7), V.PUBKEY_ALG_ED25519);
  assert.equal(stored[0], 0x02);
  assert.equal(stored.length, 33);
});

test('a stored ed25519 key decodes back to its raw bytes', () => {
  const raw = bytes(32, 7);
  const stored = V.encodePublicKey(raw, V.PUBKEY_ALG_ED25519);
  assert.equal(V.toBase64Url(V.decodePublicKey(stored)), V.toBase64Url(raw));
});

test('an ed25519 key of the wrong length is refused, not truncated', () => {
  assert.throws(() => V.encodePublicKey(bytes(31, 7), V.PUBKEY_ALG_ED25519));
});

test('an unknown algorithm byte is refused', () => {
  const stored = V.encodePublicKey(bytes(32, 7), V.PUBKEY_ALG_ED25519);
  stored[0] = 0x7f;
  assert.throws(() => V.decodePublicKey(stored));
});
