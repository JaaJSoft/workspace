'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('../../../common/tests/js/loader');

// _patchCardStatus reads the presence store and fills the status dot, label
// and "last seen" text. The relative-time wording itself is pinned down in
// timeago.test.js; here we cover the wiring: which statuses show a "last
// seen" and which suppress it.

function patchWithStatus(status, lastSeen) {
  const ctx = loadScripts(
    [
      'workspace/common/static/ui/js/timeago.js',
      'workspace/users/ui/static/users/ui/js/avatar.js',
    ],
    { Alpine: { store: () => ({ statusOf: () => status }) } },
  );
  const dot = { className: '' };
  const label = { className: '', textContent: '' };
  const ago = { textContent: 'stale' };
  const el = {
    dataset: { userId: '7', ...(lastSeen ? { lastSeen } : {}) },
    querySelector: (sel) =>
      ({ '[data-status-dot]': dot, '[data-status-label]': label, '[data-status-ago]': ago })[sel],
  };
  ctx._patchCardStatus({ querySelector: () => el });
  return { dot, label, ago };
}

const minutesAgo = (m) => new Date(Date.now() - m * 60 * 1000).toISOString();

test('offline status shows how long ago the user was last seen', () => {
  const { label, ago } = patchWithStatus('offline', minutesAgo(5));
  assert.equal(label.textContent, 'offline');
  assert.equal(ago.textContent, '· 5m ago');
});

test('away status shows the last-seen time too', () => {
  const { ago } = patchWithStatus('away', minutesAgo(90));
  assert.equal(ago.textContent, '· 1h ago');
});

test('a last-seen under a minute is suppressed — it would contradict the status', () => {
  const { ago } = patchWithStatus('offline', minutesAgo(0.5));
  assert.equal(ago.textContent, '');
});

test('online status never shows a last-seen', () => {
  const { ago } = patchWithStatus('online', minutesAgo(5));
  assert.equal(ago.textContent, '');
});

test('no last-seen data clears the field', () => {
  const { ago } = patchWithStatus('offline', null);
  assert.equal(ago.textContent, '');
});
