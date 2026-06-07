'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('./loader');

const ctx = loadScript('workspace/common/static/ui/js/avatar.js');
const { _formatTimeAgo } = ctx;

test('reports anything under a minute as "just now"', () => {
  assert.equal(_formatTimeAgo(0), 'just now');
  assert.equal(_formatTimeAgo(1), 'just now');
  assert.equal(_formatTimeAgo(59), 'just now');
});

test('formats minutes, singular at exactly one', () => {
  assert.equal(_formatTimeAgo(60), '1 minute ago');
  assert.equal(_formatTimeAgo(119), '1 minute ago');
  assert.equal(_formatTimeAgo(120), '2 minutes ago');
  assert.equal(_formatTimeAgo(3599), '59 minutes ago');
});

test('formats hours, singular at exactly one', () => {
  assert.equal(_formatTimeAgo(3600), '1 hour ago');
  assert.equal(_formatTimeAgo(7199), '1 hour ago');
  assert.equal(_formatTimeAgo(7200), '2 hours ago');
  assert.equal(_formatTimeAgo(86399), '23 hours ago');
});

test('formats days, singular at exactly one', () => {
  assert.equal(_formatTimeAgo(86400), '1 day ago');
  assert.equal(_formatTimeAgo(172799), '1 day ago');
  assert.equal(_formatTimeAgo(172800), '2 days ago');
  assert.equal(_formatTimeAgo(8 * 86400), '8 days ago');
});

test('rounds down to the largest whole unit at each boundary', () => {
  // 90 seconds -> 1 minute (not 1.5), 90 minutes -> 1 hour, 36 hours -> 1 day.
  assert.equal(_formatTimeAgo(90), '1 minute ago');
  assert.equal(_formatTimeAgo(90 * 60), '1 hour ago');
  assert.equal(_formatTimeAgo(36 * 3600), '1 day ago');
});
