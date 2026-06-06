'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('./loader');

const ctx = loadScript('workspace/common/static/ui/js/uuid.js');
const { isValidUuid } = ctx;

test('accepts uuids of every defined version (1-8)', () => {
  for (const version of ['1', '2', '3', '4', '5', '6', '7', '8']) {
    const uuid = `123e4567-e89b-${version}2d3-a456-426614174000`;
    assert.equal(isValidUuid(uuid), true, `version ${version}`);
  }
});

test('accepts the nil and max uuids', () => {
  assert.equal(isValidUuid('00000000-0000-0000-0000-000000000000'), true);
  assert.equal(isValidUuid('ffffffff-ffff-ffff-ffff-ffffffffffff'), true);
});

test('is case-insensitive', () => {
  assert.equal(isValidUuid('123E4567-E89B-42D3-A456-426614174000'), true);
});

test('accepts every valid variant nibble (8, 9, a, b)', () => {
  for (const variant of ['8', '9', 'a', 'b']) {
    const uuid = `123e4567-e89b-42d3-${variant}456-426614174000`;
    assert.equal(isValidUuid(uuid), true, `variant ${variant}`);
  }
});

test('rejects invalid version or variant nibbles', () => {
  // version 0 and 9 are undefined
  assert.equal(isValidUuid('123e4567-e89b-02d3-a456-426614174000'), false);
  assert.equal(isValidUuid('123e4567-e89b-92d3-a456-426614174000'), false);
  // variant nibble outside [89ab]
  assert.equal(isValidUuid('123e4567-e89b-42d3-c456-426614174000'), false);
  assert.equal(isValidUuid('123e4567-e89b-42d3-7456-426614174000'), false);
});

test('rejects malformed strings', () => {
  assert.equal(isValidUuid(''), false);
  assert.equal(isValidUuid('not-a-uuid'), false);
  assert.equal(isValidUuid('123e4567-e89b-42d3-a456-42661417400'), false); // too short
  assert.equal(isValidUuid('123e4567-e89b-42d3-a456-4266141740000'), false); // too long
  assert.equal(isValidUuid('{123e4567-e89b-42d3-a456-426614174000}'), false); // braces
  assert.equal(isValidUuid('123e4567e89b42d3a456426614174000'), false); // no dashes
});

test('rejects non-string input', () => {
  assert.equal(isValidUuid(null), false);
  assert.equal(isValidUuid(undefined), false);
  assert.equal(isValidUuid(42), false);
  assert.equal(isValidUuid({}), false);
});
