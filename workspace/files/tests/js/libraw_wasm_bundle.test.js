// Integrity checks on the vendored libraw-wasm build, copied out of the npm
// tarball by scripts/frontend/vendor-copy.mjs. The image viewer imports
// index.js as a module on the first camera RAW, and that module starts its
// worker, which loads LibRaw, from files sitting next to it.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const VENDOR = path.join(
  REPO_ROOT, 'workspace', 'files', 'ui', 'static', 'files', 'ui', 'js', 'vendor', 'libraw-wasm'
);
const SCRIPTS = ['index.js', 'worker.js', 'libraw.js'];

test('the module, its worker and LibRaw sit side by side', () => {
  for (const file of [...SCRIPTS, 'libraw.wasm']) {
    assert.ok(fs.existsSync(path.join(VENDOR, file)), `missing artifact: ${file}`);
  }
  const index = fs.readFileSync(path.join(VENDOR, 'index.js'), 'utf8');
  assert.match(index, /new URL\("\.\/worker\.js",import\.meta\.url\)/, 'worker no longer started from ./worker.js');
  assert.match(index, /export\{\w+ as default\}/, 'LibRaw default export missing');
});

test('the WASM binary is copied as bytes', () => {
  const wasm = fs.readFileSync(path.join(VENDOR, 'libraw.wasm'));
  assert.deepStrictEqual([...wasm.subarray(0, 4)], [0x00, 0x61, 0x73, 0x6d]);
});

test('nothing is fetched from elsewhere', () => {
  for (const file of SCRIPTS) {
    const src = fs.readFileSync(path.join(VENDOR, file), 'utf8');
    assert.doesNotMatch(src, /https?:\/\//, `remote URL left in ${file}`);
    assert.doesNotMatch(src, /sourceMappingURL/, `source map reference left in ${file}`);
  }
});
