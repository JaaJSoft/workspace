// Integrity checks on the vendored FullCalendar stack: the standard bundle,
// luxon and the luxon3 bridge, copied out of the npm tarballs by
// scripts/frontend/vendor-copy.mjs. calendar/ui/index.html loads the three as
// classic scripts in that order, and calendar_events.js reads the
// `FullCalendar` global.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const VENDOR = path.join(
  REPO_ROOT, 'workspace', 'calendar', 'ui', 'static', 'calendar', 'ui', 'js', 'vendor', 'fullcalendar'
);
const MANIFEST = path.join(REPO_ROOT, 'scripts', 'frontend', 'package.json');
const ARTIFACTS = ['fullcalendar.js', 'luxon.js', 'luxon3.js'];
const THIRD_PARTY_HOSTS = ['cdn.jsdelivr.net', 'unpkg.com', 'esm.sh'];

const pinned = JSON.parse(fs.readFileSync(MANIFEST, 'utf8')).dependencies;
const read = (name) => fs.readFileSync(path.join(VENDOR, name), 'utf8');

test('the three artifacts exist and are not empty', () => {
  for (const name of ARTIFACTS) {
    const file = path.join(VENDOR, name);
    assert.ok(fs.existsSync(file), `missing artifact: ${file}`);
    assert.ok(fs.statSync(file).size > 1_000, `${name} suspiciously small`);
  }
});

test('fullcalendar.js is the pinned standard bundle and defines the global', () => {
  const src = read('fullcalendar.js');
  // The banner line ends right after the version: "v6.1.15\n" cannot match "v6.1.150".
  assert.ok(
    src.includes(`FullCalendar Standard Bundle v${pinned.fullcalendar}\n`),
    'version banner does not match the pinned fullcalendar'
  );
  assert.match(src, /^var FullCalendar=/m, 'the global build assigns `var FullCalendar`');
});

test('luxon.js is the pinned luxon build and defines the global', () => {
  const src = read('luxon.js');
  assert.match(src, /^var luxon=/m, 'the global build assigns `var luxon`');
  assert.ok(src.includes(`VERSION="${pinned.luxon}"`), 'VERSION does not match the pinned luxon');
});

test('luxon3.js is the pinned bridge and registers itself as a global plugin', () => {
  const src = read('luxon3.js');
  assert.ok(
    src.includes(`FullCalendar Luxon 3 Plugin v${pinned['@fullcalendar/luxon3']}\n`),
    'version banner does not match the pinned @fullcalendar/luxon3'
  );
  assert.match(src, /^FullCalendar\.Luxon3=/m, 'the bridge hangs itself off the FullCalendar global');
  // calendar_events.js never lists the plugin: it relies on this push.
  assert.match(src, /globalPlugins\.push\(/);
});

test('the bridge and the bundle are the same FullCalendar version', () => {
  assert.equal(pinned['@fullcalendar/luxon3'], pinned.fullcalendar);
});

test('no artifact is ESM, references a source map, or points at a CDN', () => {
  for (const name of ARTIFACTS) {
    const src = read(name);
    assert.doesNotMatch(src, /^\s*(import|export)[\s{*]/m, `${name}: ESM declaration found`);
    assert.doesNotMatch(src, /sourceMappingURL/, `${name}: source map reference left in`);
    for (const host of THIRD_PARTY_HOSTS) {
      assert.ok(!src.includes(host), `${name}: references ${host}`);
    }
  }
});
