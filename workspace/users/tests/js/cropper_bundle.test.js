// Integrity checks on the vendored Cropper.js build, copied out of the npm
// tarball by scripts/frontend/vendor-copy.mjs. The avatar croppers in the
// profile settings and the chat conversation settings read the `Cropper`
// global from a classic script and link the stylesheet next to it.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const STATIC = path.join(REPO_ROOT, 'workspace', 'users', 'ui', 'static', 'users', 'ui');
const SCRIPT = path.join(STATIC, 'js', 'vendor', 'cropper', 'cropper.js');
const STYLESHEET = path.join(STATIC, 'css', 'vendor', 'cropper', 'cropper.css');
const MANIFEST = path.join(REPO_ROOT, 'scripts', 'frontend', 'package.json');

const pinned = JSON.parse(fs.readFileSync(MANIFEST, 'utf8')).dependencies.cropperjs;
// The banner line ends right after the version: "v1.6.2\n" cannot match "v1.6.20".
const banner = `Cropper.js v${pinned}\n`;

test('the script is the pinned UMD build', () => {
  assert.ok(fs.existsSync(SCRIPT), `missing artifact: ${SCRIPT}`);
  const src = fs.readFileSync(SCRIPT, 'utf8');
  assert.ok(src.includes(banner), 'version banner does not match the pinned cropperjs');
  // Loaded without type="module" on pages with no AMD loader: the UMD
  // wrapper must fall through to the global assignment.
  assert.match(src, /typeof define&&define\.amd/, 'UMD wrapper missing');
  assert.doesNotMatch(src, /^\s*(import|export)[\s{*]/m, 'ESM declaration found');
});

test('the stylesheet is the pinned build and needs nothing remote', () => {
  assert.ok(fs.existsSync(STYLESHEET), `missing artifact: ${STYLESHEET}`);
  const src = fs.readFileSync(STYLESHEET, 'utf8');
  assert.ok(src.includes(banner), 'version banner does not match the pinned cropperjs');
  assert.match(src, /\.cropper-container\{/, 'cropper styles missing');
  assert.doesNotMatch(src, /url\(\s*["']?https?:/, 'remote url() left in the vendored stylesheet');
});

test('neither artifact references a source map', () => {
  for (const file of [SCRIPT, STYLESHEET]) {
    assert.doesNotMatch(
      fs.readFileSync(file, 'utf8'), /sourceMappingURL/,
      `${path.basename(file)}: source map reference left in`
    );
  }
});
