'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

const SCRIPT = 'workspace/core/static/core/js/user_settings.js';

function load(answer, calls = []) {
  return loadScript(SCRIPT, {
    getCSRFToken: () => 'csrf-token',
    fetch: (url, options) => {
      calls.push({ url, options });
      return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(answer);
    },
  });
}

test('saveUserSetting() puts the value under its module and key', async () => {
  const calls = [];
  await load({ ok: true, status: 200 }, calls).saveUserSetting('photos', 'tile_size', 4);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/v1/settings/photos/tile_size');
  assert.equal(calls[0].options.method, 'PUT');
  assert.equal(calls[0].options.headers['X-CSRFToken'], 'csrf-token');
  assert.deepEqual(JSON.parse(calls[0].options.body), { value: 4 });
});

test('saveUserSetting() rejects a refused value and a failed request', async () => {
  await assert.rejects(load({ ok: false, status: 400 }).saveUserSetting('photos', 'tile_size', 9));
  await assert.rejects(load(new Error('offline')).saveUserSetting('photos', 'tile_size', 4));
});
