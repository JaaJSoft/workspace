// Integrity checks on the vendored heic-to build, copied out of the npm
// tarball by scripts/frontend/vendor-copy.mjs. The image viewer injects it as
// a classic script on the first HEIC the browser cannot show, and reads the
// `HeicTo` global it declares.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const SCRIPT = path.join(
  REPO_ROOT, 'workspace', 'files', 'ui', 'static', 'files', 'ui', 'js', 'vendor', 'heic-to', 'heic-to.js'
);

test('the script is the IIFE build declaring the HeicTo global', () => {
  assert.ok(fs.existsSync(SCRIPT), `missing artifact: ${SCRIPT}`);
  const src = fs.readFileSync(SCRIPT, 'utf8');
  assert.match(src, /^var HeicTo = \(\(\) => \{/, 'HeicTo global declaration missing');
  assert.doesNotMatch(src, /^\s*(import|export)[\s{*]/m, 'ESM declaration found');
});

test('the WASM decoder is inlined, nothing is fetched from elsewhere', () => {
  const src = fs.readFileSync(SCRIPT, 'utf8');
  assert.doesNotMatch(src, /https?:\/\//, 'remote URL left in the vendored build');
  assert.doesNotMatch(src, /sourceMappingURL/, 'source map reference left in');
});
