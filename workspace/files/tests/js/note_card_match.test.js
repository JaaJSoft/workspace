const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/common/static/ui/js/note_card.js');

test('extracts uuid from /notes?file=', () => {
  assert.equal(
    ctx.noteCardFileUuidFromHref('/notes?file=550e8400-e29b-41d4-a716-446655440000'),
    '550e8400-e29b-41d4-a716-446655440000'
  );
});

test('extracts uuid from a trailing &file=', () => {
  assert.equal(
    ctx.noteCardFileUuidFromHref('/x?a=1&file=550e8400-e29b-41d4-a716-446655440000'),
    '550e8400-e29b-41d4-a716-446655440000'
  );
});

test('returns null when there is no file param', () => {
  assert.equal(ctx.noteCardFileUuidFromHref('/notes'), null);
});

test('returns null for empty input', () => {
  assert.equal(ctx.noteCardFileUuidFromHref(''), null);
});
