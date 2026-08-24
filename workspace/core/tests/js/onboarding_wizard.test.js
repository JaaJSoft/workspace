'use strict';

// The welcome tour's component. Only the completion flag is covered here: the
// step machine is trivial and the dialog itself has a browser test.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

function wizard({ reply } = {}) {
  const calls = [];
  const ctx = loadScript('workspace/core/static/core/js/onboarding_wizard.js', {
    getCSRFToken: () => 'token',
    fetch: async (url, options) => {
      calls.push({ url, options });
      if (typeof reply === 'function') return reply();
      return reply ?? { ok: true };
    },
  });
  return { app: ctx.onboardingWizard(true), calls };
}

test('a first close records the completion once', async () => {
  const { app, calls } = wizard();
  await app.markCompleteIfNeeded();
  await app.markCompleteIfNeeded();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/v1/settings/core/onboarding_completed');
  assert.equal(app.completed, true);
});

test('a completion the server refused is not recorded', async () => {
  // The flag is what stops a retry. Keeping it after a 500 means the write is
  // never attempted again, while the server - which the tour actually reads -
  // still has the tour pending.
  const { app } = wizard({ reply: { ok: false, status: 500 } });
  await app.markCompleteIfNeeded();
  assert.equal(app.completed, false);
});

test('a completion lost to the network is not recorded either', async () => {
  const { app } = wizard({
    reply: () => {
      throw new TypeError('network error');
    },
  });
  await app.markCompleteIfNeeded();
  assert.equal(app.completed, false);
});

test('a refused completion is retried on the next close', async () => {
  let attempts = 0;
  const { app, calls } = wizard({
    reply: () => {
      attempts += 1;
      return { ok: attempts > 1 };
    },
  });
  await app.markCompleteIfNeeded();
  await app.markCompleteIfNeeded();
  assert.equal(calls.length, 2);
  assert.equal(app.completed, true);
});

test('an account that already finished the tour writes nothing', async () => {
  const ctx = loadScript('workspace/core/static/core/js/onboarding_wizard.js', {
    getCSRFToken: () => 'token',
    fetch: async () => assert.fail('no request expected'),
  });
  const app = ctx.onboardingWizard(false);
  assert.equal(app.completed, true);
  await app.markCompleteIfNeeded();
});
