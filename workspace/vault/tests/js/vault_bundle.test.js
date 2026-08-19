// Integrity checks on the vendored vault crypto artifact. The parity suite
// covers behavior; this file locks down what would break loading silently -
// wrong module format, missing global, floating version, stale artifact - plus
// the size budget on the unlock path.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const BUNDLE = path.join(
  REPO_ROOT, 'workspace', 'vault', 'ui', 'static', 'vault', 'ui', 'js', 'vendor', 'vault-crypto.js'
);
const MANIFEST = path.join(REPO_ROOT, 'scripts', 'frontend', 'package.json');
const BUNDLE_REPO_PATH = 'workspace/vault/ui/static/vault/ui/js/vendor/vault-crypto.js';

// The unlock path budgets ~75 KB gzipped of non-native code. Argon2id already
// makes the user wait before anything is readable, so every byte here is
// latency on top of it. Exceeded means a dependency landed here that belongs
// in the on-demand bundle, not that the limit should be raised.
const BUDGET_GZIP_BYTES = 75 * 1024;

test('the bundle exists and is not empty', () => {
  assert.ok(fs.existsSync(BUNDLE), `missing artifact: ${BUNDLE}`);
  assert.ok(fs.statSync(BUNDLE).size > 0, 'artifact is empty');
});

test('the bundle is an IIFE, not ESM', () => {
  const src = fs.readFileSync(BUNDLE, 'utf8');
  // Loaded through <script defer> without type="module", ESM output raises a
  // SyntaxError and nothing runs. Assert the shape we want: minified ESM emits
  // `import{a}from"x"`, which a /^\s*import\s/ guard misses.
  assert.ok(src.trimStart().startsWith('(()=>{'), 'bundle does not open with the esbuild IIFE wrapper');
  assert.doesNotMatch(src, /^\s*(import|export)[\s{*]/m, 'ESM import/export declaration found');
});

test('the bundle exposes window.VaultCrypto', () => {
  const src = fs.readFileSync(BUNDLE, 'utf8');
  assert.match(src, /window\.VaultCrypto\s*=/, 'window.VaultCrypto is never assigned');
});

test('the bundle stays within the unlock-path size budget', () => {
  const gzipped = zlib.gzipSync(fs.readFileSync(BUNDLE)).length;
  assert.ok(
    gzipped <= BUDGET_GZIP_BYTES,
    `bundle is ${gzipped} B gzipped, budget is ${BUDGET_GZIP_BYTES} B - move the offending dependency to the on-demand bundle`
  );
});

test('versions are pinned exactly', () => {
  // The manifest is shared with Alpine, Milkdown and Tailwind: a floating range
  // anywhere in it makes every vendored artifact non-reproducible, so the check
  // deliberately covers the whole file rather than the vault dependencies only.
  const manifest = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'));
  const deps = { ...manifest.dependencies, ...manifest.devDependencies };
  assert.ok(Object.keys(deps).length > 0, 'no dependencies declared');
  for (const [name, range] of Object.entries(deps)) {
    assert.match(
      range, /^\d+\.\d+\.\d+$/,
      `${name} is "${range}": a floating version makes the bundle non-reproducible`
    );
  }
});

test('the dependency lockfile is committed', () => {
  assert.ok(
    fs.existsSync(path.join(REPO_ROOT, 'scripts', 'frontend', 'package-lock.json')),
    'package-lock.json missing: rebuilds would not be reproducible'
  );
});

const ONBOARDING = path.join(
  REPO_ROOT, 'workspace', 'vault', 'ui', 'static', 'vault', 'ui', 'js', 'vendor', 'vault-onboarding.js'
);

test('the on-demand bundle exists, is an IIFE and exposes its global', () => {
  assert.ok(fs.existsSync(ONBOARDING), `missing artifact: ${ONBOARDING}`);
  const src = fs.readFileSync(ONBOARDING, 'utf8');
  assert.ok(src.trimStart().startsWith('(()=>{'), 'bundle does not open with the esbuild IIFE wrapper');
  assert.match(src, /window\.VaultOnboarding\s*=/, 'window.VaultOnboarding is never assigned');
});

test('the PDF generator and the strength estimator stay off the main bundle', () => {
  // Either one would break the unlock-path budget on its own. Landing them
  // there is the regression this test exists to catch, and the size budget
  // alone would not name the culprit.
  const src = fs.readFileSync(BUNDLE, 'utf8');
  assert.doesNotMatch(src, /zxcvbn/i, 'the strength estimator leaked into the main bundle');
  assert.doesNotMatch(src, /jspdf/i, 'the PDF generator leaked into the main bundle');
});

test('randomBytes names the deployment problem when the CSPRNG is absent', () => {
  // Outside a secure context there is no crypto.getRandomValues and no
  // crypto.subtle. Without this guard the first key derivation fails with an
  // opaque TypeError instead of saying what is wrong.
  const { loadScript } = require('../../../common/tests/js/loader');
  const deprived = loadScript(BUNDLE_REPO_PATH, {
    crypto: undefined,
    TextEncoder: globalThis.TextEncoder,
    btoa: globalThis.btoa,
    atob: globalThis.atob,
  });
  assert.throws(() => deprived.VaultCrypto.randomBytes(32), /secure context/);
});

test('randomBytes returns the length asked for, and draws each time', () => {
  const { loadScript } = require('../../../common/tests/js/loader');
  const ctx = loadScript(BUNDLE_REPO_PATH, {
    crypto: globalThis.crypto,
    TextEncoder: globalThis.TextEncoder,
    btoa: globalThis.btoa,
    atob: globalThis.atob,
  });
  const V = ctx.VaultCrypto;
  assert.equal(V.randomBytes(32).length, 32);
  assert.notEqual(V.toBase64Url(V.randomBytes(32)), V.toBase64Url(V.randomBytes(32)));
});
