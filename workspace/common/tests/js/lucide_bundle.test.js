// Integrity checks on the vendored Lucide artifact. Unlike the other entries
// of scripts/frontend it is copied out of the npm tarball rather than bundled,
// so its bytes can be checked against the digest the published build carries.
const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const BUNDLE = path.join(
  REPO_ROOT, 'workspace', 'common', 'static', 'ui', 'js', 'vendor', 'lucide', 'lucide.js'
);
const MANIFEST = path.join(REPO_ROOT, 'scripts', 'frontend', 'package.json');

// The published build's SHA-384. Update it - from the npm tarball, never from
// a browser - in the same commit that bumps the pinned version.
const PUBLISHED_SHA384 = '7PArHXNzg1s/WgbAH9xkBpx4T6MJ0jPAxaEM9yld+zvdFEjSf+wzzv0hDnem/6rw';
// What vendor-lucide.mjs strips, and what the digest above needs back.
const SOURCE_MAP_COMMENT = '//# sourceMappingURL=lucide.min.js.map\n';

test('the artifact exists and is not empty', () => {
  assert.ok(fs.existsSync(BUNDLE), `missing artifact: ${BUNDLE}`);
  assert.ok(fs.statSync(BUNDLE).size > 100_000, 'artifact suspiciously small');
});

test('the artifact is the published build, minus its source map comment', () => {
  // Every other check in this file reads the artifact as text and would pass
  // on a hand-patched copy. Put the stripped comment back and the digest must
  // be the published one: nothing else may differ.
  const restored = Buffer.concat([
    fs.readFileSync(BUNDLE),
    Buffer.from(SOURCE_MAP_COMMENT, 'utf8'),
  ]);
  const digest = crypto.createHash('sha384').update(restored).digest('base64');
  assert.equal(digest, PUBLISHED_SHA384, 'vendored Lucide does not match the published build');
});

test('the source map reference is gone', () => {
  // collectstatic resolves it through the manifest storage and fails the whole
  // command when the map is missing - which it is, at 3.9 MB we do not ship.
  assert.doesNotMatch(fs.readFileSync(BUNDLE, 'utf8'), /sourceMappingURL/);
});

test('the artifact is UMD, not ESM', () => {
  const src = fs.readFileSync(BUNDLE, 'utf8');
  // base.html loads it without type="module" and every template reads the
  // `lucide` global; ESM output would raise a SyntaxError instead.
  assert.doesNotMatch(src, /^\s*(import|export)[\s{*]/m, 'ESM import/export declaration found');
});

test('the artifact exposes createIcons', () => {
  const src = fs.readFileSync(BUNDLE, 'utf8');
  // lucide_boot.js and every LucideUtils helper call it.
  assert.match(src, /createIcons/, 'createIcons is nowhere in the artifact');
});

test('the artifact was copied from the pinned lucide version', () => {
  const pinned = JSON.parse(fs.readFileSync(MANIFEST, 'utf8')).dependencies.lucide;
  assert.match(pinned, /^\d+\.\d+\.\d+$/, 'lucide is not pinned in the manifest');
  // Closes the manifest -> artifact loop the digest cannot: it says which
  // version those bytes are supposed to be.
  assert.ok(
    fs.readFileSync(BUNDLE, 'utf8').includes(pinned),
    `artifact does not carry lucide ${pinned}`
  );
});
