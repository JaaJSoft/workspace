// Integrity checks on the vendored Leaflet build, copied out of the npm
// tarball by scripts/frontend/vendor-copy.mjs. The map of a photo's location
// (photos/ui/js/photo_map.js) reads the `L` global from a classic script, and
// leaflet.css points at its images by relative url(), which collectstatic
// resolves at deploy time.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const STATIC = path.join(REPO_ROOT, 'workspace', 'photos', 'ui', 'static', 'photos', 'ui');
const SCRIPT = path.join(STATIC, 'js', 'vendor', 'leaflet', 'leaflet.js');
const STYLESHEET_DIR = path.join(STATIC, 'css', 'vendor', 'leaflet');
const STYLESHEET = path.join(STYLESHEET_DIR, 'leaflet.css');
const MANIFEST = path.join(REPO_ROOT, 'scripts', 'frontend', 'package.json');

const pinned = JSON.parse(fs.readFileSync(MANIFEST, 'utf8')).dependencies.leaflet;

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
// The IEND chunk closing every PNG: length 0, type, CRC.
const PNG_END = Buffer.from([0, 0, 0, 0, 0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82]);

function stylesheetImages() {
  const css = fs.readFileSync(STYLESHEET, 'utf8');
  return [...new Set([...css.matchAll(/url\((images\/[^)]+)\)/g)].map((m) => m[1]))];
}

test('the script is the pinned global build', () => {
  assert.ok(fs.existsSync(SCRIPT), `missing artifact: ${SCRIPT}`);
  const src = fs.readFileSync(SCRIPT, 'utf8');
  assert.ok(src.includes(`version="${pinned}"`), 'version does not match the pinned leaflet');
  assert.match(src, /window\.L=/, 'the L global is never assigned');
  assert.doesNotMatch(src, /^\s*(import|export)[\s{*]/m, 'ESM declaration found');
});

test('the stylesheet needs nothing remote', () => {
  assert.ok(fs.existsSync(STYLESHEET), `missing artifact: ${STYLESHEET}`);
  const src = fs.readFileSync(STYLESHEET, 'utf8');
  assert.match(src, /\.leaflet-container/, 'leaflet styles missing');
  assert.doesNotMatch(src, /url\(\s*["']?https?:/, 'remote url() left in the vendored stylesheet');
});

test('every image the stylesheet references is vendored, as an intact PNG', () => {
  const images = stylesheetImages();
  assert.ok(images.length > 0, 'the stylesheet references no image');
  for (const image of images) {
    const file = path.join(STYLESHEET_DIR, image);
    // A missing file fails collectstatic's manifest storage on deploy.
    assert.ok(fs.existsSync(file), `missing image: ${image}`);
    // Copied through a text decoder, the bytes come out mangled.
    const bytes = fs.readFileSync(file);
    assert.ok(bytes.subarray(0, 8).equals(PNG_SIGNATURE), `${image}: not a PNG signature`);
    assert.ok(bytes.subarray(-12).equals(PNG_END), `${image}: no IEND chunk at the end`);
  }
});

test('neither the script nor the stylesheet references a source map', () => {
  for (const file of [SCRIPT, STYLESHEET]) {
    assert.doesNotMatch(
      fs.readFileSync(file, 'utf8'), /sourceMappingURL/,
      `${path.basename(file)}: source map reference left in`
    );
  }
});
