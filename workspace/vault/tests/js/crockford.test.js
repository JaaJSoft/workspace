// The recovery secret is the one value a user may retype from paper. These
// tests pin the alphabet, the check symbol and the forgiving decode against
// the frozen vectors the Python reference produced.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { loadScript } = require('../../../common/tests/js/loader');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');

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
const VECTORS = JSON.parse(
  fs.readFileSync(
    path.join(REPO_ROOT, 'workspace', 'vault', 'tests', 'crypto_vectors.json'),
    'utf8'
  )
);

// Decoded inside the vm: a Uint8Array built out here carries this realm's
// prototypes, and the bundle is entitled to branch on them.
const inVm = (b64) =>
  vm.runInContext(`vaultCrypto.fromBase64Url(${JSON.stringify(b64)})`, ctx);

test('recovery secret vectors replay exactly', () => {
  for (const vector of VECTORS.recovery_secrets) {
    assert.equal(V.crockfordEncode(inVm(vector.raw_b64)), vector.expected_text, vector.id);
    assert.equal(
      V.toBase64Url(V.crockfordDecode(vector.expected_text)),
      vector.raw_b64,
      vector.id
    );
  }
});

test('the alphabet leaves out the characters a human confuses', () => {
  for (const vector of VECTORS.recovery_secrets) {
    for (const forbidden of ['I', 'L', 'O', 'U']) {
      assert.ok(
        !vector.expected_text.slice(0, -1).includes(forbidden),
        `${vector.id} contains ${forbidden}`
      );
    }
  }
});

test('a mistyped character is refused', () => {
  const text = VECTORS.recovery_secrets[1].expected_text;
  const broken = (text[0] === 'Z' ? 'Y' : 'Z') + text.slice(1);
  assert.throws(() => V.crockfordDecode(broken), /check/i);
});

test('confusable characters decode as their twin', () => {
  const vector = VECTORS.recovery_secrets[1];
  const mangled = vector.expected_text.replace(/0/g, 'O').replace(/1/g, 'I');
  assert.equal(V.toBase64Url(V.crockfordDecode(mangled)), vector.raw_b64);
});

test('grouping and case are ignored on input', () => {
  const vector = VECTORS.recovery_secrets[1];
  const grouped = vector.expected_text.match(/.{1,4}/g).join('-').toLowerCase();
  assert.equal(V.toBase64Url(V.crockfordDecode(grouped)), vector.raw_b64);
});

test('an illegal character is refused rather than skipped', () => {
  const text = VECTORS.recovery_secrets[1].expected_text;
  assert.throws(() => V.crockfordDecode(text.slice(0, -1) + '!'), /illegal/i);
});

test('a confusable written in place of the check symbol decodes too', () => {
  // The check symbol comes from the wider alphabet, so it lands on "0" or "1"
  // roughly twice in 37 - and those are exactly the two a transcriber writes
  // as "O" and "I". Folding the body but not the check refuses the very slip
  // the alphabet exists to absorb.
  const vector = VECTORS.recovery_secrets.find(
    (v) => v.id === 'recovery-confusable-check'
  );
  assert.equal(vector.expected_text.slice(-1), '0', 'vector no longer checks on a 0');
  const mangled = vector.expected_text.slice(0, -1) + 'O';
  assert.equal(V.toBase64Url(V.crockfordDecode(mangled)), vector.raw_b64);
});
