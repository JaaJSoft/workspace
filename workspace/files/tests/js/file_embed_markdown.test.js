// The markdown of a file embed (file_embed_markdown.js): which image
// sources are embeds, and how a parsed note (mdast) is rewritten so the
// editor draws them. An ES module, so it is imported rather than run through
// the classic-script loader.
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { loadScript } = require('../../../common/tests/js/loader');

const MODULE = pathToFileURL(
  path.join(__dirname, '..', '..', 'ui', 'static', 'files', 'ui', 'js', 'file_embed_markdown.js')
).href;
const UUID = '01929b6e-5c3a-7d4e-8f00-0123456789ab';
const OTHER = '01929b6e-5c3a-7d4e-8f00-ba9876543210';

let md;
test.before(async () => {
  // The page provides isValidUuid (common/static/ui/js/uuid.js).
  const { isValidUuid } = loadScript('workspace/common/static/ui/js/uuid.js');
  globalThis.window = { isValidUuid };
  md = await import(MODULE);
});

const image = (url, alt = 'x', title = null) => ({ type: 'image', url, alt, title });
const paragraph = (...children) => ({ type: 'paragraph', children });
const text = (value) => ({ type: 'text', value });

test('embedUuidFromSrc reads the file of a content URL, lowercased', () => {
  assert.equal(md.embedUuidFromSrc(`/api/v1/files/${UUID}/content`), UUID);
  assert.equal(md.embedUuidFromSrc(`/api/v1/files/${UUID.toUpperCase()}/content?v=abc`), UUID);
});

test('embedUuidFromSrc rejects every other URL', () => {
  for (const src of [
    '',
    null,
    'https://example.com/a.png',
    `/api/v1/files/${UUID}/download`,
    `/api/v1/files/${UUID}/contents`,
    `https://evil.example/api/v1/files/${UUID}/content`,
    '/api/v1/files/not-a-uuid/content',
  ]) {
    assert.equal(md.embedUuidFromSrc(src), null, String(src));
  }
});

test('embedSrc is what embedUuidFromSrc reads', () => {
  assert.equal(md.embedUuidFromSrc(md.embedSrc(UUID)), UUID);
});

test('a paragraph holding only an embed becomes a file-embed node', () => {
  const tree = { type: 'root', children: [paragraph(image(md.embedSrc(UUID), 'report.pdf', 'cap'))] };
  md.rewriteEmbeds(tree);
  assert.deepEqual(tree.children, [
    { type: md.EMBED_NODE, uuid: UUID, name: 'report.pdf', title: 'cap' },
  ]);
});

test("Crepe's image block of an embed is taken over", () => {
  const tree = {
    type: 'root',
    children: [{ type: 'image-block', url: md.embedSrc(UUID), alt: 'photo.png', title: '' }],
  };
  md.rewriteEmbeds(tree);
  assert.deepEqual(tree.children, [{ type: md.EMBED_NODE, uuid: UUID, name: 'photo.png', title: '' }]);
});

test('embeds on consecutive lines become one node each, in order', () => {
  const tree = {
    type: 'root',
    children: [
      paragraph(image(md.embedSrc(UUID), 'a'), text('\n'), image(md.embedSrc(OTHER), 'b')),
    ],
  };
  md.rewriteEmbeds(tree);
  assert.deepEqual(
    tree.children.map((n) => [n.type, n.uuid]),
    [[md.EMBED_NODE, UUID], [md.EMBED_NODE, OTHER]]
  );
});

test('an embed in the middle of a sentence stays an inline image', () => {
  const p = paragraph(text('see '), image(md.embedSrc(UUID)), text(' here'));
  const tree = { type: 'root', children: [p] };
  md.rewriteEmbeds(tree);
  assert.equal(tree.children[0], p);
  assert.equal(p.children[1].type, 'image');
});

test('an ordinary image is left alone', () => {
  const block = { type: 'image-block', url: 'https://example.com/a.png', alt: '1.00' };
  const tree = { type: 'root', children: [paragraph(image('/static/logo.png')), block] };
  md.rewriteEmbeds(tree);
  assert.equal(tree.children[0].type, 'paragraph');
  assert.equal(tree.children[1], block);
});

test('embeds nested in a quote or a list are found', () => {
  const tree = {
    type: 'root',
    children: [
      { type: 'blockquote', children: [paragraph(image(md.embedSrc(UUID)))] },
      { type: 'list', children: [{ type: 'listItem', children: [paragraph(image(md.embedSrc(OTHER)))] }] },
    ],
  };
  md.rewriteEmbeds(tree);
  assert.equal(tree.children[0].children[0].type, md.EMBED_NODE);
  assert.equal(tree.children[1].children[0].children[0].uuid, OTHER);
});
