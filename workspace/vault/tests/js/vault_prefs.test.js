'use strict';

// The vault preferences stored on the account: applied at once, put back
// with an error when the server does not keep them.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('../../../common/tests/js/loader');

function boot(reply) {
  const calls = [];
  const timeouts = [];
  const ctx = loadScripts([
    'workspace/core/static/core/js/user_settings.js',
    'workspace/vault/ui/static/vault/ui/js/vault_prefs.js',
  ], {
    getCSRFToken: () => 'token',
    fetch: (url, options) => {
      calls.push({ url, options });
      return reply instanceof Error ? Promise.reject(reply) : Promise.resolve(reply);
    },
    vaultSession: { setIdleTimeout: (minutes) => timeouts.push(minutes) },
  });
  const prefs = ctx.vaultPrefsMixin();
  prefs.sortField = 'default';
  return { prefs, calls, timeouts };
}

test('a preference is put under its key and applied', async () => {
  const { prefs, calls, timeouts } = boot({ ok: true, status: 200 });
  await prefs.updatePref('lock_after_minutes', 15);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/v1/settings/vault/lock_after_minutes');
  assert.equal(calls[0].options.method, 'PUT');
  assert.deepEqual(JSON.parse(calls[0].options.body), { value: 15 });
  assert.equal(prefs.prefs.lockAfterMinutes, 15);
  assert.deepEqual(timeouts, [15]);
  assert.equal(prefs.error, undefined);
});

test('a refused preference is put back and reported', async () => {
  const { prefs, timeouts } = boot({ ok: false, status: 400 });
  await prefs.updatePref('default_sort', 'name');

  assert.equal(prefs.prefs.defaultSort, 'default');
  assert.equal(prefs.sortField, 'default');
  assert.equal(timeouts.at(-1), 5);
  assert.match(prefs.error, /could not be saved/);
});

test('a preference lost to the network is put back and reported', async () => {
  const { prefs } = boot(new TypeError('offline'));
  await prefs.updatePref('lock_after_minutes', 30);

  assert.equal(prefs.prefs.lockAfterMinutes, 5);
  assert.match(prefs.error, /could not be saved/);
});
