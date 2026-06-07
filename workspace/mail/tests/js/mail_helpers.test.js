'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

// mail_helpers.js only touches `document` inside function bodies (popover
// builders), never at load time, so it loads without any DOM stub. We exercise
// the two pure helpers it declares at the top level: _cleanName and _initial.
const ctx = loadScript('workspace/mail/ui/static/mail/ui/js/mail_helpers.js');
const { _cleanName, _initial } = ctx;

test('_cleanName keeps a plain display name untouched', () => {
  assert.equal(_cleanName('John Doe'), 'John Doe');
});

test('_cleanName strips wrapping quotes and surrounding whitespace', () => {
  assert.equal(_cleanName('  "John Doe"  '), 'John Doe');
  assert.equal(_cleanName("'Jane'"), 'Jane');
});

test('_cleanName strips leading and trailing punctuation', () => {
  assert.equal(_cleanName('<John>'), 'John');
  assert.equal(_cleanName('-John-'), 'John');
});

test('_cleanName preserves accented letters at the edges', () => {
  assert.equal(_cleanName('Éric'), 'Éric');
  assert.equal(_cleanName('"Renée"'), 'Renée');
});

test('_cleanName collapses an all-punctuation string to empty', () => {
  assert.equal(_cleanName('123'), '');
  assert.equal(_cleanName('---'), '');
});

test('_cleanName returns empty for falsy or non-string input', () => {
  assert.equal(_cleanName(''), '');
  assert.equal(_cleanName(null), '');
  assert.equal(_cleanName(undefined), '');
  assert.equal(_cleanName(42), '');
});

test('_initial returns the uppercased first letter', () => {
  assert.equal(_initial('john'), 'J');
  assert.equal(_initial('Alice'), 'A');
});

test('_initial skips leading non-letters to the first letter', () => {
  assert.equal(_initial('  alice'), 'A');
  assert.equal(_initial('123abc'), 'A');
  assert.equal(_initial('@home'), 'H');
});

test('_initial uppercases an accented first letter', () => {
  assert.equal(_initial('éric'), 'É');
});

test('_initial falls back to the first char when there is no letter', () => {
  assert.equal(_initial('123'), '1');
  assert.equal(_initial('#'), '#');
});

test('_initial returns "?" for empty or missing input', () => {
  assert.equal(_initial(''), '?');
  assert.equal(_initial(null), '?');
  assert.equal(_initial(undefined), '?');
});
