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

test('userAvatarColorClass is deterministic and covers a 12-color palette', () => {
  assert.equal(ctx.userAvatarColorClass(7), ctx.userAvatarColorClass(7));
  const palette = new Set();
  for (let id = 0; id < 12; id++) palette.add(ctx.userAvatarColorClass(id));
  assert.equal(palette.size, 12);
  for (const cls of palette) assert.match(cls, /^bg-[a-z]+-500$/);
});

test('userAvatarColorClass wraps around the palette and accepts numeric strings', () => {
  assert.equal(ctx.userAvatarColorClass(12), ctx.userAvatarColorClass(0));
  assert.equal(ctx.userAvatarColorClass(25), ctx.userAvatarColorClass(1));
  assert.equal(ctx.userAvatarColorClass('5'), ctx.userAvatarColorClass(5));
});

test('userAvatarColorClass falls back to bg-neutral on invalid input', () => {
  assert.equal(ctx.userAvatarColorClass(undefined), 'bg-neutral');
  assert.equal(ctx.userAvatarColorClass(null), 'bg-neutral');
  assert.equal(ctx.userAvatarColorClass(''), 'bg-neutral');
  assert.equal(ctx.userAvatarColorClass('abc'), 'bg-neutral');
  assert.equal(ctx.userAvatarColorClass(3.5), 'bg-neutral');
});

test('userAvatarHtml embeds the per-user color class in its onerror fallback', () => {
  const html = ctx.userAvatarHtml(5, 'Bob', 'w-8 h-8');
  assert.ok(html.includes(ctx.userAvatarColorClass(5)));
  assert.ok(html.includes('text-white'));
});
