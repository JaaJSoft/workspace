// Integrity checks on the compiled Tailwind stylesheet. Tailwind only keeps
// the classes it can see in the templates, so the failure mode of a broken
// build is a stylesheet that exists but silently lost half its rules. Lock
// down a few sentinels from each layer that feeds it.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const BUNDLE = path.join(REPO_ROOT, 'workspace', 'common', 'static', 'css', 'app.css');

test('the stylesheet exists and is not empty', () => {
  assert.ok(fs.existsSync(BUNDLE), `missing artifact: ${BUNDLE}`);
  assert.ok(fs.statSync(BUNDLE).size > 100_000, 'artifact suspiciously small');
});

test('the stylesheet carries the DaisyUI components and themes', () => {
  const src = fs.readFileSync(BUNDLE, 'utf8');
  assert.match(src, /\.btn\{/, 'DaisyUI component layer missing');
  assert.match(src, /\[data-theme=dark\]/, 'DaisyUI theme layer missing');
});

test('the stylesheet carries the safelisted runtime-built utilities', () => {
  const src = fs.readFileSync(BUNDLE, 'utf8');
  // Templates interpolate these at render time (`bg-{{ color }}/10`), so the
  // content scanner never sees them: only tailwind.config.js's safelist keeps
  // them alive.
  assert.match(src, /\.bg-primary\\\/10\{/, 'safelisted opacity utility purged');
  assert.match(src, /\.badge-warning\{/, 'safelisted DaisyUI colour variant purged');
});

test('the stylesheet carries the input.css additions', () => {
  const src = fs.readFileSync(BUNDLE, 'utf8');
  // Toast animations declared by hand in scripts/frontend/input.css.
  assert.match(src, /@keyframes slide-in-right/, 'input.css layer missing');
});
