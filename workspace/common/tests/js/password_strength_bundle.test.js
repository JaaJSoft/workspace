// Integrity checks on the vendored password strength artifact: module format,
// the global it registers, what it must not carry, and that it actually
// estimates - with feedback in English rather than as message keys.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const { loadScript } = require('./loader');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const BUNDLE_REPO_PATH = 'workspace/common/static/ui/js/vendor/password-strength/password-strength.js';
const BUNDLE = path.join(REPO_ROOT, BUNDLE_REPO_PATH);

// Fetched on demand by every page with a password field, so it is the one
// cost those pages pay beyond the shared shell. zxcvbn's dictionaries are
// most of it; exceeded means something else was bundled alongside them.
const BUDGET_GZIP_BYTES = 260 * 1024;

test('the bundle exists, is an IIFE and exposes its global', () => {
  assert.ok(fs.existsSync(BUNDLE), `missing artifact: ${BUNDLE}`);
  const src = fs.readFileSync(BUNDLE, 'utf8');
  assert.ok(src.trimStart().startsWith('(()=>{'), 'bundle does not open with the esbuild IIFE wrapper');
  assert.doesNotMatch(src, /^\s*(import|export)[\s{*]/m, 'ESM import/export declaration found');
  assert.match(src, /window\.passwordStrengthTools\s*=/, 'window.passwordStrengthTools is never assigned');
});

test('the bundle carries the estimator and nothing else', () => {
  // jsPDF used to ride along with zxcvbn in the vault onboarding bundle. A
  // page with a password field has nothing to print.
  const src = fs.readFileSync(BUNDLE, 'utf8');
  assert.doesNotMatch(src, /jspdf/i, 'the PDF generator leaked into the strength bundle');
  const gzipped = zlib.gzipSync(fs.readFileSync(BUNDLE)).length;
  assert.ok(
    gzipped <= BUDGET_GZIP_BYTES,
    `bundle is ${gzipped} B gzipped, budget is ${BUDGET_GZIP_BYTES} B - something beyond zxcvbn landed here`
  );
});

function estimator() {
  return loadScript(BUNDLE_REPO_PATH, { setTimeout, clearTimeout }).passwordStrengthTools;
}

test('feedback comes back as sentences, not message keys', async () => {
  // Without the translations zxcvbn reports `topTen` and `straightRow`,
  // which is what the vault onboarding page printed for a while.
  const result = await estimator().estimateStrength('password');
  assert.equal(result.score, 0);
  assert.match(result.warning, /^[A-Z].*\.$/, `not a sentence: ${JSON.stringify(result.warning)}`);
  assert.doesNotMatch(result.warning, /^[a-z]+[A-Z]/, 'warning is a camelCase key');
  for (const suggestion of result.suggestions) {
    assert.match(suggestion, /^[A-Z].*\.$/, `not a sentence: ${JSON.stringify(suggestion)}`);
  }
});

test('a keyboard walk is named as one', async () => {
  const result = await estimator().estimateStrength('zxcvbnm,./');
  assert.match(result.warning, /keyboard/i);
});

test('a long random passphrase reaches the top band with nothing to warn about', async () => {
  const result = await estimator().estimateStrength('correct-horse-battery-staple-42');
  assert.equal(result.score, 4);
  assert.equal(result.warning, '');
});

test('the shape is stable whatever zxcvbn has to say', async () => {
  // The meter reads all three fields unconditionally; a missing one would be
  // an undefined on screen.
  const result = await estimator().estimateStrength('Tr0ub4dor&3');
  assert.equal(typeof result.score, 'number');
  assert.equal(typeof result.warning, 'string');
  assert.ok(Array.isArray(result.suggestions));
});
