// Integrity checks on the vendored Monaco editor: the ES module the text
// viewer imports, the language chunks it loads on demand, the five worker
// scripts text_viewer.html maps by language label, and the stylesheet with
// its icon font. Running the editor needs a real DOM (the Playwright suites
// cover that); here we lock down what would silently break loading.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const STATIC = path.join(REPO_ROOT, 'workspace', 'files', 'ui', 'static', 'files', 'ui');
const JS_DIR = path.join(STATIC, 'js', 'vendor', 'monaco');
const ENTRY = path.join(JS_DIR, 'monaco-editor.js');
const CSS_DIR = path.join(STATIC, 'css', 'vendor', 'monaco');
const STYLESHEET = path.join(CSS_DIR, 'editor.css');
const WORKERS = ['editor', 'json', 'css', 'html', 'ts'].map((name) => `${name}.worker.js`);
const THIRD_PARTY_HOSTS = ['cdn.jsdelivr.net', 'unpkg.com', 'esm.sh'];

const scripts = () => fs.readdirSync(JS_DIR).filter((name) => name.endsWith('.js'));
const read = (name) => fs.readFileSync(path.join(JS_DIR, name), 'utf8');

test('the entry is an ES module exporting the editor surface', () => {
  assert.ok(fs.existsSync(ENTRY), `missing artifact: ${ENTRY}`);
  const exports = read('monaco-editor.js').match(/export\{([^}]*)\}/g)?.join(' ') ?? '';
  // text_viewer.html reads exactly these off the dynamic import.
  for (const name of ['editor', 'languages', 'KeyMod', 'KeyCode']) {
    assert.match(exports, new RegExp(`\\b${name}\\b`), `${name} is not exported`);
  }
});

test('every chunk referenced by any script exists', () => {
  let lazy = 0;
  for (const name of scripts()) {
    const src = read(name);
    for (const [, chunk] of src.matchAll(/"\.\/(chunk-[A-Z0-9]+\.js)"/g)) {
      assert.ok(fs.existsSync(path.join(JS_DIR, chunk)), `${name} references missing ${chunk}`);
    }
    lazy += (src.match(/import\("\.\/chunk-/g) ?? []).length;
  }
  // Each language contribution sits behind a dynamic import: without
  // --splitting they would all be inlined into the entry.
  assert.ok(lazy > 50, `only ${lazy} lazy chunk imports: was it built with --splitting?`);
});

test('the five workers exist and compile as classic scripts', () => {
  for (const name of WORKERS) {
    const file = path.join(JS_DIR, name);
    assert.ok(fs.existsSync(file), `missing worker: ${file}`);
    assert.ok(fs.statSync(file).size > 100_000, `${name} suspiciously small`);
    // Spawned with `new Worker(url)` and no `type: "module"`: an import or
    // export declaration, or a bare import.meta, is a SyntaxError there.
    // Compiling (not running) as a classic script is the same check.
    assert.doesNotThrow(
      () => new vm.Script(read(name), { filename: name }),
      `${name} does not parse as a classic script`
    );
  }
});

test('the stylesheet ships the codicon font and nothing remote', () => {
  assert.ok(fs.existsSync(STYLESHEET), `missing artifact: ${STYLESHEET}`);
  const src = fs.readFileSync(STYLESHEET, 'utf8');
  assert.doesNotMatch(src, /url\(\s*["']?https?:/, 'remote url() left in the vendored stylesheet');
  const fonts = [...src.matchAll(/url\("\.\/(fonts\/[^"]+)"\)/g)].map((m) => m[1]);
  assert.ok(fonts.includes('fonts/codicon.ttf'), 'codicon font is not referenced');
  for (const font of new Set(fonts)) {
    assert.ok(fs.existsSync(path.join(CSS_DIR, font)), `missing font ${font}`);
  }
});

test('no script references a source map or a CDN', () => {
  for (const name of scripts()) {
    const src = read(name);
    // Only a comment at the start of a line is a reference: the TypeScript
    // worker mentions the marker in the compiler's own strings, and
    // collectstatic anchors its pattern at line start too.
    assert.doesNotMatch(src, /^\/\/# sourceMappingURL=/m, `${name}: source map reference left in`);
    for (const host of THIRD_PARTY_HOSTS) {
      assert.ok(!src.includes(host), `${name}: references ${host}`);
    }
  }
});
