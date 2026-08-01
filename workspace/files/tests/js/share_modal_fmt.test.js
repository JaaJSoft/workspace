'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function makeModal(userTz) {
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/share_modal.js', {});
  ctx.getUserTimeZone = () => userTz;
  return ctx.shareModal();
}

test('formatLinkExpiry keeps the chosen day for behind-UTC zones', () => {
  const modal = makeModal('America/Los_Angeles');
  // The expiry is stored as UTC midnight of the picked day; a naive
  // user-zone conversion would show July 31.
  const label = modal.formatLinkExpiry('2026-08-01T00:00:00Z');
  assert.match(label, /8\/1\/2026|2026-08-01|0?1\/0?8\/2026/);
  assert.doesNotMatch(label, /7\/31|31\/0?7|2026-07-31/);
});

test('formatLinkExpiry falls back to Permanent', () => {
  assert.equal(makeModal(undefined).formatLinkExpiry(null), 'Permanent');
});
