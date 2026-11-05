// The "[[" / "![[" trigger of the editor autocomplete (wikilink_match.js).
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const MODULE = pathToFileURL(
  path.join(__dirname, '..', '..', 'ui', 'static', 'files', 'ui', 'js', 'wikilink_match.js')
).href;

let m;
test.before(async () => {
  m = await import(MODULE);
});

test('"[[" opens a note link', () => {
  assert.deepEqual({ ...m.matchTrigger('see [[meet') }, { query: 'meet', length: 6, embed: false });
});

test('"![[" opens a file embed and spans the "!"', () => {
  assert.deepEqual({ ...m.matchTrigger('see ![[rep') }, { query: 'rep', length: 6, embed: true });
  assert.deepEqual({ ...m.matchTrigger('![[') }, { query: '', length: 3, embed: true });
});

test('a closed or absent trigger is not active', () => {
  assert.equal(m.matchTrigger('[[done]]'), null);
  assert.equal(m.matchTrigger('![[done]] after'), null);
  assert.equal(m.matchTrigger('no trigger'), null);
});
