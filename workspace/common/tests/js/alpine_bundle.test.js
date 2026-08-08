// Integrity checks on the vendored Alpine artifact. Running Alpine would need
// a real DOM, which this runner has no npm dependencies for - the Playwright
// suites cover behavior. Here we only lock down what would silently break
// loading: wrong module format, missing global, floating version.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const BUNDLE = path.join(
  REPO_ROOT, 'workspace', 'common', 'static', 'ui', 'js', 'vendor', 'alpine', 'alpine.js'
);
const MANIFEST = path.join(REPO_ROOT, 'scripts', 'alpine', 'package.json');

test('the bundle exists and is not empty', () => {
  assert.ok(fs.existsSync(BUNDLE), `missing artifact: ${BUNDLE}`);
  assert.ok(fs.statSync(BUNDLE).size > 10_000, 'artifact suspiciously small');
});

test('the bundle is an IIFE, not ESM', () => {
  const src = fs.readFileSync(BUNDLE, 'utf8');
  // Through <script defer> without type="module", ESM output would raise a
  // SyntaxError and Alpine would never start. Assert the shape we want rather
  // than enumerate the ones we don't: minified ESM emits `import{a}from"x"`,
  // which a /^\s*import\s/ guard misses.
  assert.ok(src.trimStart().startsWith('(()=>{'), 'bundle does not open with the esbuild IIFE wrapper');
  assert.doesNotMatch(src, /^\s*(import|export)[\s{*]/m, 'ESM import/export declaration found');
});

test('the bundle exposes window.Alpine', () => {
  const src = fs.readFileSync(BUNDLE, 'utf8');
  // stores.js, avatar.js and chat/sse.js all read the Alpine global.
  assert.match(src, /window\.Alpine\s*=/, 'window.Alpine is never assigned');
});

test('the bundle was built from the pinned alpine version', () => {
  const src = fs.readFileSync(BUNDLE, 'utf8');
  const pinned = JSON.parse(fs.readFileSync(MANIFEST, 'utf8')).dependencies.alpinejs;
  // Closes the manifest->artifact loop: without this, a stale or hand-edited
  // bundle passes every other check in this file.
  assert.match(src, new RegExp(`version:"${pinned.replace(/\./g, '\\.')}"`),
    `bundle does not carry Alpine ${pinned}`);
});

test('versions are pinned exactly', () => {
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
    fs.existsSync(path.join(REPO_ROOT, 'scripts', 'alpine', 'package-lock.json')),
    'package-lock.json missing: rebuilds would not be reproducible'
  );
});
