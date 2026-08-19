// Differential corpus: hundreds of generated inputs, encoded by the Python
// reference, replayed here. The hand-written vectors pin the shapes someone
// thought of; this file covers the ones nobody did - which is where both of
// this module's encoding bugs actually lived.
//
// A failure prints the case id. Regenerate or explore new ground with:
//   uv run python -m workspace.vault.tests.reference.generate_fuzz_corpus <seed> <count>
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { loadScript } = require('../../../common/tests/js/loader');

const vm = require('node:vm');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const CORPUS_TEXT = fs.readFileSync(
  path.join(REPO_ROOT, 'workspace', 'vault', 'tests', 'fuzz_corpus.json'), 'utf8'
);

const ctx = loadScript('workspace/vault/ui/static/vault/ui/js/vendor/vault-crypto.js', {
  crypto: globalThis.crypto,
  TextEncoder: globalThis.TextEncoder,
  TextDecoder: globalThis.TextDecoder,
  btoa: globalThis.btoa,
  atob: globalThis.atob,
  __corpusText: CORPUS_TEXT,
});

// Parsed INSIDE the vm, deliberately. An array built out here carries the test
// realm's Array, and cbor-x branches on `constructor === Array`: cross-realm it
// misses, falls through to its iterator path and emits an indefinite-length
// array, which canonical CBOR forbids. The browser has one realm and encodes
// definite lengths, so parsing outside would make this suite disagree with
// production and report failures that do not exist.
const CORPUS = vm.runInContext('JSON.parse(__corpusText)', ctx);

const V = ctx.VaultCrypto;
const b64 = (bytes) => V.toBase64Url(bytes);

test('every generated cbor payload encodes to the reference bytes', () => {
  const mismatches = [];
  for (const item of CORPUS.cbor) {
    if (b64(V.canonicalCbor(item.payload)) !== item.expected_b64) mismatches.push(item.id);
  }
  assert.deepStrictEqual(
    mismatches, [],
    `${mismatches.length}/${CORPUS.cbor.length} payloads diverged (seed ${CORPUS.seed})`
  );
});

test('every generated ciphertext matches the reference wire bytes and reopens', async () => {
  const mismatches = [];
  for (const item of CORPUS.aead) {
    const key = V.fromBase64Url(item.key_b64);
    const ad = V.fromBase64Url(item.ad_b64);
    const raw = await V.seal(key, V.fromBase64Url(item.plaintext_b64), ad, {
      iv: V.fromBase64Url(item.iv_b64),
      keyVersion: item.key_version,
      kdfId: item.kdf_id,
    });
    if (b64(raw) !== item.expected_wire_b64) {
      mismatches.push(item.id);
      continue;
    }
    if (b64(await V.open(key, raw, ad)) !== item.plaintext_b64) mismatches.push(`${item.id}/reopen`);
  }
  assert.deepStrictEqual(
    mismatches, [],
    `${mismatches.length}/${CORPUS.aead.length} ciphertexts diverged (seed ${CORPUS.seed})`
  );
});

test('the corpus is large enough to be worth running', () => {
  // Guards against a regeneration that silently produced an empty file: the
  // two tests above pass trivially over nothing.
  assert.ok(CORPUS.cbor.length >= 100, `only ${CORPUS.cbor.length} cbor cases`);
  assert.ok(CORPUS.aead.length >= 50, `only ${CORPUS.aead.length} aead cases`);
});

test('a negative integer the reference cannot match is refused', () => {
  // cbor-x writes eight bytes for these through its BigInt path where the
  // canonical form is four, so the encoder refuses rather than diverge.
  for (const value of [-(2 ** 31) - 1, -(2 ** 32)]) {
    assert.throws(() => V.canonicalCbor({ v: 1, n: value }), /no encoding/, String(value));
  }
});

test('the edges just outside that band still encode', () => {
  for (const value of [-(2 ** 31), -(2 ** 32) - 1]) {
    assert.ok(V.canonicalCbor({ v: 1, n: value }).length > 0, String(value));
  }
});

test('keys that collide after normalisation are refused', () => {
  assert.throws(
    () => V.canonicalCbor({ 'café': 1, 'café': 2 }),
    /collide/
  );
});
