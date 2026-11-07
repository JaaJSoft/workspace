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

function fakeResponse(chunks, headers = {}) {
  let cancelled = false;
  const queue = chunks.map((size) => new Uint8Array(size));
  return {
    headers: { get: (name) => headers[name] ?? null },
    body: {
      getReader: () => ({
        read: async () => (queue.length ? { done: false, value: queue.shift() } : { done: true }),
        cancel: () => { cancelled = true; },
      }),
    },
    wasCancelled: () => cancelled,
  };
}

function loadWithBlob() {
  return loadScript(READER, { document: { addEventListener() {} }, URL, Blob }).ebookReader;
}

test('readBook returns the whole body within the limit', async () => {
  const reader = loadWithBlob();
  const blob = await reader.readBook(
    fakeResponse([400, 400], { 'Content-Type': 'application/epub+zip' }),
    '1024',
  );
  assert.equal(blob.size, 800);
  assert.equal(blob.type, 'application/epub+zip');
});

test('readBook refuses an announced length past the limit before reading', async () => {
  const reader = loadWithBlob();
  const response = fakeResponse([10], { 'Content-Length': '2048' });
  await assert.rejects(reader.readBook(response, '1024'), /larger than the reader opens/);
});

test('readBook stops a body without a length as soon as it goes past', async () => {
  const reader = loadWithBlob();
  const response = fakeResponse([600, 600, 600]);
  await assert.rejects(reader.readBook(response, '1024'), /larger than the reader opens/);
  assert.equal(response.wasCancelled(), true);
});
