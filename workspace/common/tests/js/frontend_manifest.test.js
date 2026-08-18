// Reproducibility checks on the frontend build project (scripts/frontend),
// which emits every vendored asset: Alpine, the Milkdown editor and its theme
// CSS, the Tailwind stylesheet. Per-artifact integrity lives in the sibling
// *_bundle.test.js files.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const PROJECT = path.join(__dirname, '..', '..', '..', '..', 'scripts', 'frontend');
const MANIFEST = path.join(PROJECT, 'package.json');

test('versions are pinned exactly', () => {
  const manifest = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'));
  const deps = { ...manifest.dependencies, ...manifest.devDependencies };
  assert.ok(Object.keys(deps).length > 0, 'no dependencies declared');
  for (const [name, range] of Object.entries(deps)) {
    assert.match(
      range, /^\d+\.\d+\.\d+$/,
      `${name} is "${range}": a floating version makes the bundles non-reproducible`
    );
  }
});

test('the dependency lockfile is committed', () => {
  assert.ok(
    fs.existsSync(path.join(PROJECT, 'package-lock.json')),
    'package-lock.json missing: rebuilds would not be reproducible'
  );
});

test('a single build script regenerates every artifact', () => {
  const scripts = JSON.parse(fs.readFileSync(MANIFEST, 'utf8')).scripts;
  // The rebuild workflow and the docs only know `npm run build`; a per-artifact
  // script that is not chained into it would silently go stale.
  const chained = scripts.build.split('&&').map((s) => s.trim());
  for (const name of Object.keys(scripts).filter((s) => s.startsWith('build:'))) {
    assert.ok(chained.includes(`npm run ${name}`), `${name} is not part of \`npm run build\``);
  }
});
