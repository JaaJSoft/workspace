// Integrity checks on the vendored Milkdown editor artifacts: the ESM bundle
// imported by markdown_viewer.html and the theme stylesheet linked by the
// files and notes pages. Running the editor needs a real DOM (the Playwright
// suites cover that); here we lock down what would silently break loading.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const STATIC = path.join(REPO_ROOT, 'workspace', 'files', 'ui', 'static', 'files', 'ui');
const ENTRY = path.join(STATIC, 'js', 'vendor', 'milkdown', 'milkdown-editor.js');
const THEME_DIR = path.join(STATIC, 'css', 'vendor', 'milkdown');
const THEME = path.join(THEME_DIR, 'theme.css');

test('the editor entry is an ESM module exporting the crepe surface', () => {
  assert.ok(fs.existsSync(ENTRY), `missing artifact: ${ENTRY}`);
  const src = fs.readFileSync(ENTRY, 'utf8');
  // markdown_viewer.html reads exactly these three names off the dynamic import.
  const exports = src.match(/export\{([^}]*)\}/g)?.join(' ') ?? '';
  for (const name of ['Crepe', 'SlashProvider', 'slashFactory']) {
    assert.match(exports, new RegExp(`\\b${name}\\b`), `${name} is not exported`);
  }
});

test('the split chunks referenced by the entry all exist', () => {
  const src = fs.readFileSync(ENTRY, 'utf8');
  const chunks = [...src.matchAll(/from"\.\/(chunk-[A-Z0-9]+\.js)"/g)].map((m) => m[1]);
  assert.ok(chunks.length > 0, 'entry imports no chunk: was it built with --splitting?');
  for (const chunk of chunks) {
    assert.ok(fs.existsSync(path.join(path.dirname(ENTRY), chunk)), `missing chunk ${chunk}`);
  }
});

test('the theme stylesheet bundles ProseMirror, KaTeX and the crepe common layer', () => {
  assert.ok(fs.existsSync(THEME), `missing artifact: ${THEME}`);
  const src = fs.readFileSync(THEME, 'utf8');
  assert.match(src, /\.ProseMirror\{/, 'ProseMirror base styles missing');
  assert.match(src, /font-family:KaTeX_Main/, 'KaTeX styles missing');
  assert.match(src, /\.milkdown-code-block/, 'crepe common theme missing');
});

test('the theme stylesheet ships its fonts and nothing from a CDN', () => {
  const src = fs.readFileSync(THEME, 'utf8');
  assert.doesNotMatch(src, /https?:\/\//, 'remote url() left in the vendored stylesheet');
  const fonts = [...src.matchAll(/url\("\.\/(fonts\/[^"]+)"\)/g)].map((m) => m[1]);
  assert.ok(fonts.length > 0, 'no font file referenced');
  for (const font of new Set(fonts)) {
    assert.ok(fs.existsSync(path.join(THEME_DIR, font)), `missing font ${font}`);
  }
});
