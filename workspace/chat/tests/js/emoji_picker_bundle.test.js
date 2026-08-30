// Integrity checks on the vendored emoji picker: the ES module bundle that
// registers <emoji-picker> (scripts/frontend/emoji-picker.js) and the emoji
// list it fetches at runtime from its data-source attribute
// (scripts/frontend/vendor-copy.mjs, set emoji-data).
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const REPO_ROOT = path.join(__dirname, '..', '..', '..', '..');
const VENDOR = path.join(
  REPO_ROOT, 'workspace', 'chat', 'ui', 'static', 'chat', 'ui', 'js', 'vendor', 'emoji-picker'
);
const BUNDLE = path.join(VENDOR, 'emoji-picker.js');
const DATA = path.join(VENDOR, 'data.json');

test('the bundle registers the <emoji-picker> element', () => {
  assert.ok(fs.existsSync(BUNDLE), `missing artifact: ${BUNDLE}`);
  const src = fs.readFileSync(BUNDLE, 'utf8');
  assert.match(src, /customElements\.define\("emoji-picker"/, 'the element is never registered');
  assert.doesNotMatch(src, /sourceMappingURL/, 'source map reference left in');
});

test('the emoji list is the emojibase data the picker expects', () => {
  assert.ok(fs.existsSync(DATA), `missing artifact: ${DATA}`);
  const data = JSON.parse(fs.readFileSync(DATA, 'utf8'));
  assert.ok(Array.isArray(data), 'data.json is not an array');
  assert.ok(data.length > 1_500, `only ${data.length} emoji: is this the full list?`);
  // The picker indexes on these fields when it loads the list into IndexedDB.
  for (const key of ['emoji', 'group', 'order', 'annotation']) {
    assert.ok(key in data[0], `emoji entries carry no "${key}"`);
  }
});
