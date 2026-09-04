'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('./loader');

// Shadows the context's own Intl with a counting wrapper: the helper looks
// the global up on every construction, so the count sees each one.
function load() {
  const ctx = loadScript('workspace/common/static/ui/js/zoned_formatter.js');
  const counter = { built: 0 };
  ctx.Intl = {
    DateTimeFormat: function (...args) {
      counter.built++;
      return new Intl.DateTimeFormat(...args);
    },
  };
  return { ctx, counter };
}

test('formats in the requested zone with the given locale and options', () => {
  const { ctx } = load();
  const label = ctx.zonedFormatter('en-US', { month: 'short', day: '2-digit' });
  // 20:00 UTC on Jan 31 is already Feb 1 in Tokyo.
  assert.equal(label('Asia/Tokyo').format(new Date('2026-01-31T20:00:00Z')), 'Feb 01');
  assert.equal(label('UTC').format(new Date('2026-01-31T20:00:00Z')), 'Jan 31');
});

test('builds one formatter per zone, however many times it is asked', () => {
  const { ctx, counter } = load();
  const dayKey = ctx.zonedFormatter('en-CA');
  for (let i = 0; i < 50; i++) dayKey('Asia/Tokyo').format(new Date());
  assert.equal(counter.built, 1);

  dayKey('America/New_York').format(new Date());
  dayKey('America/New_York').format(new Date());
  assert.equal(counter.built, 2);

  dayKey('Asia/Tokyo').format(new Date());
  assert.equal(counter.built, 2);
});

test('each call site owns its cache', () => {
  const { ctx, counter } = load();
  const dayKey = ctx.zonedFormatter('en-CA');
  const label = ctx.zonedFormatter('en-US', { month: 'short' });
  dayKey('UTC');
  label('UTC');
  assert.equal(counter.built, 2);
  assert.equal(label('UTC').format(new Date('2026-02-01T12:00:00Z')), 'Feb');
});

test('without a zone the formatter is rebuilt every time', () => {
  // The browser zone binds at construction and can change while the page
  // is open (a laptop crossing a border), so that one must not be cached.
  const { ctx, counter } = load();
  const dayKey = ctx.zonedFormatter('en-CA');
  dayKey(undefined).format(new Date());
  dayKey(undefined).format(new Date());
  dayKey(null).format(new Date());
  assert.equal(counter.built, 3);
});
