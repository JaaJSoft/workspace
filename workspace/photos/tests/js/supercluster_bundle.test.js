// Integrity checks on the vendored Supercluster build, copied out of the npm
// tarball by scripts/frontend/vendor-copy.mjs. The library map
// (photos/ui/js/library_map.js) reads the `Supercluster` global from a
// classic script.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { loadScript } = require('../../../common/tests/js/loader');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const RELATIVE = 'workspace/photos/ui/static/photos/ui/js/vendor/supercluster/supercluster.js';
const SCRIPT = path.join(REPO_ROOT, RELATIVE);

test('the script is a global build with no source map', () => {
  assert.ok(fs.existsSync(SCRIPT), `missing artifact: ${SCRIPT}`);
  const src = fs.readFileSync(SCRIPT, 'utf8');
  assert.doesNotMatch(src, /^\s*(import|export)[\s{*]/m, 'ESM declaration found');
  assert.doesNotMatch(src, /sourceMappingURL/, 'source map reference left in');
});

test('it publishes Supercluster, which groups what lies close and splits it on zooming in', () => {
  const ctx = loadScript(RELATIVE);
  assert.strictEqual(typeof ctx.Supercluster, 'function', 'the Supercluster global is never assigned');
  const index = new ctx.Supercluster({ radius: 60, maxZoom: 16 });
  index.load([
    { type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [2.2945, 48.8584] } },
    { type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [2.3376, 48.8606] } },
  ]);
  const world = index.getClusters([-180, -85, 180, 85], 3);
  assert.strictEqual(world.length, 1);
  assert.strictEqual(world[0].properties.point_count, 2);
  assert.strictEqual(index.getClusters([-180, -85, 180, 85], 16).length, 2);
});
