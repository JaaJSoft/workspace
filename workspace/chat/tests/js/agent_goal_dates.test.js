'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript, loadScripts } = require('../../../common/tests/js/loader');

// The goal form's datetime-local inputs are read in the user's configured
// timezone, the one the panel displays dates in, not the browser's.
function buildApp(userTz) {
  const localtime = loadScripts([
    'workspace/common/static/ui/js/zoned_formatter.js',
    'workspace/common/static/ui/js/localtime.js',
  ], {
    document: {
      documentElement: { getAttribute: (name) => (name === 'data-timezone' ? userTz : null) },
      body: {},
      querySelectorAll: () => [],
    },
    MutationObserver: class { observe() {} },
  });
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/bot.js', {
    document: { getElementById: () => null },
  });
  ctx.wallClockToIso = localtime.wallClockToIso;
  ctx.isoToWallClock = localtime.isoToWallClock;
  ctx.getUserTimeZone = localtime.getUserTimeZone;
  return { ...ctx.chatBotMixin() };
}

test('a typed deadline is the wall clock of the configured timezone', () => {
  const app = buildApp('Asia/Tokyo');

  assert.equal(app.localInputToIso('2026-09-01T18:00'), '2026-09-01T09:00:00.000Z');
});

test('a stored instant fills the input on the configured timezone wall clock', () => {
  const app = buildApp('Asia/Tokyo');

  assert.equal(app.isoToLocalInput('2026-09-01T09:00:00Z'), '2026-09-01T18:00');
});

test('an unreadable value gives no date', () => {
  const app = buildApp('Asia/Tokyo');

  assert.equal(app.localInputToIso('not a date'), null);
  assert.equal(app.localInputToIso(''), null);
});
