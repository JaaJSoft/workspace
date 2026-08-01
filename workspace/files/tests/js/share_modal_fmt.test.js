'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function makeModal(userTz) {
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/share_modal.js', {});
  ctx.getUserTimeZone = () => userTz;
  return ctx.shareModal();
}

test('formatLinkExpiry shows the picked day for end-of-day expiries', () => {
  const modal = makeModal('America/Los_Angeles');
  // Stored as 23:59:59 on Aug 5 in Los Angeles (= 06:59:59Z on Aug 6):
  // the label must be the picked day, whatever the offsets involved.
  const label = modal.formatLinkExpiry('2026-08-06T06:59:59Z');
  assert.match(label, /8\/5\/2026|2026-08-05|0?5\/0?8\/2026/);
  assert.doesNotMatch(label, /8\/6|0?6\/0?8|2026-08-06/);
});

test('formatLinkExpiry falls back to Permanent', () => {
  assert.equal(makeModal(undefined).formatLinkExpiry(null), 'Permanent');
});
