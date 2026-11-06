// The pure helpers of the e-book reader page (files/ui/js/ebook_reader.js).
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const READER = 'workspace/files/ui/static/files/ui/js/ebook_reader.js';
const ORIGIN = 'https://workspace.example';

function load() {
  return loadScript(READER, { document: { addEventListener() {} }, URL }).ebookReader;
}

test('sourceUrl keeps a path on the site and resolves it', () => {
  const reader = load();
  assert.equal(
    reader.sourceUrl('/api/v1/files/abc/content', ORIGIN),
    `${ORIGIN}/api/v1/files/abc/content`,
  );
});

test('sourceUrl refuses anything off the site', () => {
  const reader = load();
  for (const src of [
    '',
    null,
    '//evil.example/book.epub',
    'https://evil.example/book.epub',
    'javascript:alert(1)',
    'data:application/epub+zip;base64,AAAA',
  ]) {
    assert.equal(reader.sourceUrl(src, ORIGIN), null, String(src));
  }
});

test('stepFontScale moves one step and stops at both ends', () => {
  const reader = load();
  assert.equal(reader.stepFontScale(100, 1), 110);
  assert.equal(reader.stepFontScale(100, -1), 90);
  assert.equal(reader.stepFontScale(200, 1), 200);
  assert.equal(reader.stepFontScale(80, -1), 80);
});

test('stepFontScale starts from the default for a value off the scale', () => {
  const reader = load();
  assert.equal(reader.stepFontScale(103, 1), 110);
});

test('parseFontScale accepts a stored step and nothing else', () => {
  const reader = load();
  assert.equal(reader.parseFontScale('135'), 135);
  assert.equal(reader.parseFontScale(null), 100);
  assert.equal(reader.parseFontScale('1000'), 100);
  assert.equal(reader.parseFontScale('large'), 100);
});

test('bookStyles carries the theme colours and the text size', () => {
  const reader = load();
  const css = reader.bookStyles({
    colorScheme: 'dark',
    color: 'rgb(1, 2, 3)',
    linkColor: 'rgb(4, 5, 6)',
    fontScale: 120,
  });
  assert.match(css, /color-scheme: dark;/);
  assert.match(css, /color: rgb\(1, 2, 3\);/);
  assert.match(css, /font-size: 120%;/);
  assert.match(css, /a:link, a:visited \{\s*color: rgb\(4, 5, 6\);/);
});

test('title reads a plain title and a language map', () => {
  const reader = load();
  assert.equal(reader.title({ title: 'Dune' }), 'Dune');
  assert.equal(reader.title({ title: { fr: 'Dune (fr)' } }), 'Dune (fr)');
  assert.equal(reader.title({}), '');
  assert.equal(reader.title(undefined), '');
});
