// Integrity checks on the vendored foliate-js build (scripts/frontend,
// `npm run build:foliate`). The e-book reader imports foliate-view.js
// dynamically and reads makeBook off it; each book format's parser is a lazy
// chunk next to it, so a chunk missing from the commit only fails when a
// book of that format is opened.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const DIR = path.join(
  REPO_ROOT, 'workspace', 'files', 'ui', 'static', 'files', 'ui', 'js', 'vendor', 'foliate'
);
const ENTRY = path.join(DIR, 'foliate-view.js');

test('the entry is an ES module exporting makeBook', () => {
  assert.ok(fs.existsSync(ENTRY), `missing artifact: ${ENTRY}`);
  const src = fs.readFileSync(ENTRY, 'utf8');
  assert.match(src, /export\s*\{[^}]*\bas makeBook\b|export\s*\{[^}]*\bmakeBook\b/);
  assert.match(src, /customElements\.define\("foliate-view"/);
});

test('every chunk a module imports is committed next to it', () => {
  for (const name of fs.readdirSync(DIR)) {
    const src = fs.readFileSync(path.join(DIR, name), 'utf8');
    for (const [, chunk] of src.matchAll(/["']\.\/(chunk-[A-Z0-9]+\.js)["']/g)) {
      assert.ok(fs.existsSync(path.join(DIR, chunk)), `${name} imports missing ${chunk}`);
    }
  }
});

test('nothing is imported from another origin', () => {
  for (const name of fs.readdirSync(DIR)) {
    const src = fs.readFileSync(path.join(DIR, name), 'utf8');
    assert.doesNotMatch(src, /import\(\s*["']https?:/, `${name} imports a remote module`);
    assert.doesNotMatch(src, /from\s*["']https?:/, `${name} imports a remote module`);
    assert.doesNotMatch(src, /sourceMappingURL/, `${name} keeps a source map reference`);
  }
});
