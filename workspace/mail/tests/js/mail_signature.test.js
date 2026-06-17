const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/mail/ui/static/mail/ui/js/mail_signature.js');
const sig = ctx.mailSignature;

test('buildBlock trims and wraps, empty stays empty', () => {
  assert.equal(sig.buildBlock('  Jean  '), '\n-- \nJean\n');
  assert.equal(sig.buildBlock(''), '');
  assert.equal(sig.buildBlock(null), '');
});

test('applySignature appends on a fresh compose', () => {
  const r = sig.applySignature('', 'Jean');
  assert.equal(r.body, '\n-- \nJean\n');
  assert.equal(r.block, '\n-- \nJean\n');
});

test('applySignature inserts before the quote on a reply', () => {
  const body = '\n\n---\nOn date, X wrote:\n> hi';
  const r = sig.applySignature(body, 'Jean');
  // Signature block sits before the quote marker.
  assert.equal(r.body, '\n-- \nJean\n\n\n---\nOn date, X wrote:\n> hi');
  assert.ok(r.body.indexOf('-- \nJean') < r.body.indexOf('---\nOn date'));
});

test('applySignature with empty signature leaves body untouched', () => {
  const r = sig.applySignature('hello', '');
  assert.equal(r.body, 'hello');
  assert.equal(r.block, '');
});

test('swapSignature replaces an existing block', () => {
  const first = sig.applySignature('', 'Jean');       // body has Jean block
  const r = sig.swapSignature(first.body, first.block, 'Marie');
  assert.equal(r.body, '\n-- \nMarie\n');
  assert.equal(r.block, '\n-- \nMarie\n');
});

test('swapSignature falls back to insertion when old block is absent', () => {
  const r = sig.swapSignature('hello', '', 'Marie');
  assert.equal(r.body, 'hello\n-- \nMarie\n');
  assert.equal(r.block, '\n-- \nMarie\n');
});
