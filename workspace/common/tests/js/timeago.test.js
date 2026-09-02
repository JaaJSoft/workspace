'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('./loader');

// Pin the timezone so the date-fallback assertions don't depend on the
// machine running the tests.
const ctx = loadScript('workspace/common/static/ui/js/timeago.js', {
  getUserTimeZone: () => 'Europe/Paris',
});

const NOW = Date.parse('2026-08-15T12:00:00Z');
const ago = (sec) => ctx.formatTimeAgo(new Date(NOW - sec * 1000).toISOString(), NOW);

test('reports anything under a minute as "just now"', () => {
  assert.equal(ago(0), 'just now');
  assert.equal(ago(59), 'just now');
});

test('future timestamps read as "just now"', () => {
  assert.equal(ago(-3600), 'just now');
});

test('formats minutes, hours and days compactly', () => {
  assert.equal(ago(60), '1m ago');
  assert.equal(ago(5 * 60), '5m ago');
  assert.equal(ago(3599), '59m ago');
  assert.equal(ago(3600), '1h ago');
  assert.equal(ago(86399), '23h ago');
  assert.equal(ago(86400), '1d ago');
  assert.equal(ago(7 * 86400 - 1), '6d ago');
});

test('rounds down to the largest whole unit at each boundary', () => {
  assert.equal(ago(90), '1m ago');
  assert.equal(ago(90 * 60), '1h ago');
  assert.equal(ago(36 * 3600), '1d ago');
});

test('falls back to a month-day label after a week, in the user timezone', () => {
  // 23:30 UTC on Jan 31 is already Feb 1 in Paris.
  assert.equal(ctx.formatTimeAgo('2026-01-31T23:30:00Z', NOW), 'Feb 01');
});

test('dates outside the current year carry the year', () => {
  assert.equal(ctx.formatTimeAgo('2025-02-01T12:00:00Z', NOW), 'Feb 01, 2025');
});

test('year comparison happens in the user timezone', () => {
  // 23:30 UTC on Dec 31 2025 is already Jan 1 2026 in Paris - same year
  // as "now", so no year suffix.
  assert.equal(ctx.formatTimeAgo('2025-12-31T23:30:00Z', NOW), 'Jan 01');
});

test('accepts Date instances', () => {
  assert.equal(ctx.formatTimeAgo(new Date(NOW - 5 * 60 * 1000), NOW), '5m ago');
});

test('returns empty string for missing or invalid input', () => {
  assert.equal(ctx.formatTimeAgo('', NOW), '');
  assert.equal(ctx.formatTimeAgo(null, NOW), '');
  assert.equal(ctx.formatTimeAgo('not-a-date', NOW), '');
});

test('formatLastSeenAgo skips the first minute and prefixes a dot', () => {
  assert.equal(ctx.formatLastSeenAgo(new Date(NOW - 30 * 1000).toISOString(), NOW), '');
  assert.equal(ctx.formatLastSeenAgo(new Date(NOW - 5 * 60 * 1000).toISOString(), NOW), '· 5m ago');
  assert.equal(ctx.formatLastSeenAgo(null, NOW), '');
  assert.equal(ctx.formatLastSeenAgo('not-a-date', NOW), '');
});

// Counts Intl.DateTimeFormat constructions. Shadows the context's own
// Intl: the script looks the global up on every call, so the count sees
// each constructor invocation.
function countFormatters(context) {
  const counter = { built: 0 };
  context.Intl = {
    DateTimeFormat: function (...args) {
      counter.built++;
      return new Intl.DateTimeFormat(...args);
    },
  };
  return counter;
}

// Older than a week, so every value reaches the absolute-date branch.
function oldValues(count) {
  return Array.from({ length: count }, (_, i) => `2026-01-${String(i % 28 + 1).padStart(2, '0')}T20:00:00Z`);
}

test('builds one date-parts formatter per zone, not one per value', () => {
  let zone = 'Europe/Paris';
  const zoned = loadScript('workspace/common/static/ui/js/timeago.js', {
    getUserTimeZone: () => zone,
  });
  const counter = countFormatters(zoned);

  oldValues(50).forEach((v) => zoned.formatTimeAgo(v, NOW));
  assert.equal(counter.built, 1);
  assert.equal(zoned.formatTimeAgo('2026-01-31T23:30:00Z', NOW), 'Feb 01');
  assert.equal(counter.built, 1);

  // The zone is part of the key: another zone gets its own formatter, once.
  zone = 'America/New_York';
  oldValues(50).forEach((v) => zoned.formatTimeAgo(v, NOW));
  assert.equal(counter.built, 2);
  // 03:00 UTC on Feb 1 is still Jan 31 in New York.
  assert.equal(zoned.formatTimeAgo('2026-02-01T03:00:00Z', NOW), 'Jan 31');
  assert.equal(counter.built, 2);
});

test('without a configured zone the formatter is rebuilt every time', () => {
  // The browser zone binds at construction and can change while the page
  // is open (a laptop crossing a border), so that one must not be cached.
  const unzoned = loadScript('workspace/common/static/ui/js/timeago.js', {
    getUserTimeZone: () => undefined,
  });
  const counter = countFormatters(unzoned);
  unzoned.formatTimeAgo('2026-01-15T12:00:00Z', NOW);
  const afterFirst = counter.built;
  assert.ok(afterFirst > 0);
  unzoned.formatTimeAgo('2026-01-15T12:00:00Z', NOW);
  assert.equal(counter.built, afterFirst * 2);
});
