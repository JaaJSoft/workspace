'use strict';

// The Files listing re-renders once per burst of processing pushes: grouped
// while a batch upload settles file after file, but never held off until the
// batch ends.

const { test, mock } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

function coalesced(options) {
  mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 0 });
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/browser.js', {
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
    Date: globalThis.Date,
  });
  const runs = [];
  const call = ctx.coalesceCalls(() => runs.push(Date.now()), options);
  return { call, runs };
}

test('a burst of calls runs once, after it goes quiet', (t) => {
  t.after(() => mock.timers.reset());
  const { call, runs } = coalesced({ wait: 400, maxWait: 2000 });

  call();
  mock.timers.tick(100);
  call();
  mock.timers.tick(100);
  call();
  mock.timers.tick(399);
  assert.deepEqual(runs, []);

  mock.timers.tick(1);
  assert.deepEqual(runs, [600]);
});

test('a burst that never goes quiet still runs every maxWait', (t) => {
  t.after(() => mock.timers.reset());
  const { call, runs } = coalesced({ wait: 400, maxWait: 2000 });

  // A file settles every 300 ms: a plain debounce would never fire.
  for (let elapsed = 0; elapsed < 4500; elapsed += 100) {
    if (elapsed % 300 === 0) call();
    mock.timers.tick(100);
  }

  // The second burst starts with the first call after the run, at 2100.
  assert.deepEqual(runs, [2000, 4100]);
});

test('a call after a run starts a new burst', (t) => {
  t.after(() => mock.timers.reset());
  const { call, runs } = coalesced({ wait: 400, maxWait: 2000 });

  call();
  mock.timers.tick(400);
  mock.timers.tick(5000);
  call();
  mock.timers.tick(400);

  assert.deepEqual(runs, [400, 5800]);
});
