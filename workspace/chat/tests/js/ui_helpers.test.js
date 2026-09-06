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

// ── Timezone-aware formatting ─────────────────────────────────

const tzCtx = loadScript('workspace/chat/ui/static/chat/ui/js/ui_helpers.js', {
  matchMedia: matchMediaStub,
});
tzCtx.getUserTimeZone = () => 'Asia/Tokyo';
const tzMixin = tzCtx.chatUiHelpersMixin();

test('formatDate crosses the day boundary in the configured timezone', () => {
  // 20:00 UTC on Jan 31 is already Feb 1 in Tokyo.
  assert.match(tzMixin.formatDate('2026-01-31T20:00:00Z'), /February 1|1 février/);
});

test('formatDateTime crosses the day boundary in the configured timezone', () => {
  assert.match(tzMixin.formatDateTime('2026-01-31T20:00:00Z'), /Feb 1|1 févr/);
});

// ── Meeting provenance banner ─────────────────────────────────

test('the occurrence label prints nothing when the payload never carried one', () => {
  const h = ctx.chatUiHelpersMixin();
  // The list shape: event_title and join_url, and no next_start key at all.
  const conv = { meeting: { event_title: 'Standup', join_url: 'https://x/meet/a' } };
  assert.equal(h.meetingOccurrenceLabel(conv), '');
});

test('the occurrence label says so when the payload carried a null one', () => {
  const h = ctx.chatUiHelpersMixin();
  const conv = { meeting: { event_title: 'Standup', next_start: null } };
  assert.equal(h.meetingOccurrenceLabel(conv), 'No upcoming occurrence');
});

test('the occurrence label formats the start it was given', () => {
  const h = ctx.chatUiHelpersMixin();
  const conv = { meeting: { event_title: 'Standup', next_start: '2026-06-25T09:30:00Z' } };
  assert.match(h.meetingOccurrenceLabel(conv), /Jun 25|25 juin/);
});

test('the occurrence label is empty on a conversation with no meeting', () => {
  const h = ctx.chatUiHelpersMixin();
  assert.equal(h.meetingOccurrenceLabel({ meeting: null }), '');
  assert.equal(h.meetingOccurrenceLabel(null), '');
});

function clipboardCtx(writeText) {
  const c = loadScript('workspace/chat/ui/static/chat/ui/js/ui_helpers.js', {
    matchMedia: matchMediaStub,
    navigator: { clipboard: { writeText } },
  });
  const alerts = [];
  c.AppAlert = {
    success: (msg) => alerts.push(['success', msg]),
    error: (msg) => alerts.push(['error', msg]),
  };
  return { mixin: c.chatUiHelpersMixin(), alerts };
}

test('copying the join link writes it to the clipboard and confirms', async () => {
  const written = [];
  const { mixin, alerts } = clipboardCtx(async (text) => { written.push(text); });
  await mixin.copyMeetingJoinUrl({ meeting: { join_url: 'https://x/meet/abc123' } });
  assert.deepStrictEqual(Array.from(written), ['https://x/meet/abc123']);
  assert.equal(alerts[0][0], 'success');
});

test('a refused clipboard write reports the failure instead of lying', async () => {
  const { mixin, alerts } = clipboardCtx(async () => { throw new Error('denied'); });
  await mixin.copyMeetingJoinUrl({ meeting: { join_url: 'https://x/meet/abc123' } });
  assert.equal(alerts[0][0], 'error');
});

test('copying a conversation with no meeting does nothing at all', async () => {
  const written = [];
  const { mixin, alerts } = clipboardCtx(async (text) => { written.push(text); });
  await mixin.copyMeetingJoinUrl({ meeting: null });
  assert.equal(written.length, 0);
  assert.equal(alerts.length, 0);
});
