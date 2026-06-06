'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('./loader');

const ctx = loadScript('workspace/common/static/ui/js/filesize.js');
const { formatFileSize } = ctx;

// formatFileSize mirrors Django's filesizeformat (en-us): same units,
// same precision, and the same non-breaking space between number and unit.
const NBSP = '\u00a0';

test('formats bytes below 1 KB with byte/bytes wording', () => {
  assert.equal(formatFileSize(0), `0${NBSP}bytes`);
  assert.equal(formatFileSize(1), `1${NBSP}byte`);
  assert.equal(formatFileSize(117), `117${NBSP}bytes`);
  assert.equal(formatFileSize(1023), `1023${NBSP}bytes`);
});

test('formats each unit with one decimal', () => {
  assert.equal(formatFileSize(1024), `1.0${NBSP}KB`);
  assert.equal(formatFileSize(1536), `1.5${NBSP}KB`);
  assert.equal(formatFileSize(1024 ** 2), `1.0${NBSP}MB`);
  assert.equal(formatFileSize(2.5 * 1024 ** 2), `2.5${NBSP}MB`);
  assert.equal(formatFileSize(1024 ** 3), `1.0${NBSP}GB`);
  assert.equal(formatFileSize(1024 ** 4), `1.0${NBSP}TB`);
});

test('caps at PB instead of overflowing the last unit', () => {
  assert.equal(formatFileSize(1024 ** 5), `1.0${NBSP}PB`);
  assert.equal(formatFileSize(3 * 1024 ** 5), `3.0${NBSP}PB`);
  // beyond 1024 PB there is no bigger unit: keep counting in PB
  assert.equal(formatFileSize(2048 * 1024 ** 5), `2048.0${NBSP}PB`);
});

test('rounds just below a unit boundary without jumping units', () => {
  assert.equal(formatFileSize(1024 ** 2 - 1), `1024.0${NBSP}KB`);
});

test('treats falsy and non-numeric input as zero', () => {
  assert.equal(formatFileSize(null), `0${NBSP}bytes`);
  assert.equal(formatFileSize(undefined), `0${NBSP}bytes`);
  assert.equal(formatFileSize(''), `0${NBSP}bytes`);
  assert.equal(formatFileSize('garbage'), `0${NBSP}bytes`);
});

test('accepts numeric strings (dataset attributes)', () => {
  assert.equal(formatFileSize('1536'), `1.5${NBSP}KB`);
});

test('separates number and unit with a non-breaking space', () => {
  assert.ok(formatFileSize(1536).includes(NBSP));
  assert.ok(!formatFileSize(1536).includes(' '), 'no regular space expected');
});
