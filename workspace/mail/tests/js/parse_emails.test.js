'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/mail/ui/static/mail/ui/js/mail.js');

// _parseEmails runs in the vm realm, so the arrays it returns carry that
// realm's Array.prototype and fail deepStrictEqual's prototype check.
// Re-wrap into a local array before asserting.
const parseEmails = (input) => Array.from(ctx._parseEmails(input));

test('splits on commas', () => {
  assert.deepEqual(parseEmails('a@b.com, c@d.com'), ['a@b.com', 'c@d.com']);
});

test('splits on semicolons', () => {
  assert.deepEqual(parseEmails('a@b.com; c@d.com'), ['a@b.com', 'c@d.com']);
});

test('splits on mixed separators without surrounding spaces', () => {
  assert.deepEqual(parseEmails('a@b.com,c@d.com;e@f.com'), ['a@b.com', 'c@d.com', 'e@f.com']);
});

test('trims whitespace around entries', () => {
  assert.deepEqual(parseEmails('  a@b.com  '), ['a@b.com']);
});

test('drops empty entries from consecutive or trailing separators', () => {
  assert.deepEqual(parseEmails('a@b.com,,c@d.com,'), ['a@b.com', 'c@d.com']);
});

test('returns a single entry when there is no separator', () => {
  assert.deepEqual(parseEmails('a@b.com'), ['a@b.com']);
});

test('passes arrays through, dropping falsy entries', () => {
  assert.deepEqual(parseEmails(['a@b.com', '', null, undefined, 'c@d.com']), ['a@b.com', 'c@d.com']);
});

test('returns an empty array for empty or non-string input', () => {
  assert.deepEqual(parseEmails(''), []);
  assert.deepEqual(parseEmails(null), []);
  assert.deepEqual(parseEmails(undefined), []);
  assert.deepEqual(parseEmails(42), []);
});
