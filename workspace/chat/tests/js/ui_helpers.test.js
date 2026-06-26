'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// Stub matchMedia so the script loads without a real browser.
const matchMediaStub = (q) => ({
  matches: q.includes('639') ? false : true,
  media: q,
  addListener: () => {},
  removeEventListener: () => {},
});

const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/ui_helpers.js', {
  matchMedia: matchMediaStub,
});

test('chatUiHelpersMixin is exposed on window', () => {
  assert.equal(typeof ctx.chatUiHelpersMixin, 'function');
});

test('memberDisplayName returns full name when available', () => {
  const h = ctx.chatUiHelpersMixin();
  const result = h.memberDisplayName({ user: { first_name: 'Alice', last_name: 'Dupont', username: 'alice' } });
  assert.equal(result, 'Alice Dupont');
});

test('memberDisplayName falls back to username when names are blank', () => {
  const h = ctx.chatUiHelpersMixin();
  const result = h.memberDisplayName({ user: { first_name: '', last_name: '', username: 'alice42' } });
  assert.equal(result, 'alice42');
});

test('memberDisplayName falls back to username when first_name is absent', () => {
  const h = ctx.chatUiHelpersMixin();
  const result = h.memberDisplayName({ user: { username: 'bob' } });
  assert.equal(result, 'bob');
});

test('formatDate returns a non-empty string for valid ISO date', () => {
  const h = ctx.chatUiHelpersMixin();
  const result = h.formatDate('2026-06-25T12:00:00Z');
  assert.ok(typeof result === 'string' && result.length > 0, `expected non-empty string, got: ${result}`);
});

test('formatDate returns empty string for falsy input', () => {
  const h = ctx.chatUiHelpersMixin();
  assert.equal(h.formatDate(''), '');
  assert.equal(h.formatDate(null), '');
  assert.equal(h.formatDate(undefined), '');
});

test('formatDateTime returns a non-empty string for valid ISO date', () => {
  const h = ctx.chatUiHelpersMixin();
  const result = h.formatDateTime('2026-06-25T09:30:00Z');
  assert.ok(typeof result === 'string' && result.length > 0, `expected non-empty string, got: ${result}`);
});

test('formatDateTime returns empty string for falsy input', () => {
  const h = ctx.chatUiHelpersMixin();
  assert.equal(h.formatDateTime(''), '');
  assert.equal(h.formatDateTime(null), '');
  assert.equal(h.formatDateTime(undefined), '');
});

test('isMobile reads matchMedia at (max-width: 1023px)', () => {
  // matchMediaStub returns matches=true for the 1023px query (contains '639' -> false else true)
  const h = ctx.chatUiHelpersMixin();
  const result = h.isMobile();
  // Our stub: matches for '(max-width: 1023px)' -> true (doesn't contain '639')
  assert.equal(result, true);
});

test('isSmallScreen reads matchMedia at (max-width: 639px)', () => {
  // matchMediaStub returns matches=false for the 639px query (contains '639' -> false)
  const h = ctx.chatUiHelpersMixin();
  const result = h.isSmallScreen();
  assert.equal(result, false);
});
