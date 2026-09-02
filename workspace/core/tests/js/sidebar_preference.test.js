'use strict';

// The collapsed-sidebar preference the shell embeds on every module page and
// the components write back through the settings API.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

const SCRIPT = 'workspace/core/static/core/js/sidebar_preference.js';

function load({ embedded, fetch } = {}) {
  const document = {
    getElementById: (id) =>
      id === 'sidebar-collapsed-data' && embedded !== undefined ? { textContent: embedded } : null,
  };
  return loadScript(SCRIPT, { document, fetch, getCSRFToken: () => 'csrf-token' });
}

test('initial() is the value the shell rendered', () => {
  assert.equal(load({ embedded: 'true' }).sidebarPreference.initial(), true);
  assert.equal(load({ embedded: 'false' }).sidebarPreference.initial(), false);
});

test('initial() is expanded without the json_script, and on anything but true', () => {
  assert.equal(load().sidebarPreference.initial(), false);
  assert.equal(load({ embedded: '{not json' }).sidebarPreference.initial(), false);
  assert.equal(load({ embedded: '"true"' }).sidebarPreference.initial(), false);
  assert.equal(load({ embedded: '1' }).sidebarPreference.initial(), false);
});

test('save() writes the module setting through the settings API', async () => {
  const calls = [];
  const fetch = (url, options) => {
    calls.push({ url, options });
    return Promise.resolve({ ok: true });
  };
  await load({ fetch }).sidebarPreference.save('files', true);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/v1/settings/files/sidebar_collapsed');
  assert.equal(calls[0].options.method, 'PUT');
  assert.equal(calls[0].options.headers['X-CSRFToken'], 'csrf-token');
  assert.deepEqual(JSON.parse(calls[0].options.body), { value: true });
});

test('save() stores a real boolean whatever the component passes', async () => {
  const bodies = [];
  const fetch = (_url, options) => {
    bodies.push(JSON.parse(options.body).value);
    return Promise.resolve({ ok: true });
  };
  const ctx = load({ fetch });
  await ctx.sidebarPreference.save('notes', 'yes');
  await ctx.sidebarPreference.save('notes', undefined);
  assert.deepEqual(bodies, [false, false]);
});

test('save() swallows a refused write - the sidebar has already moved', async () => {
  const ctx = load({ fetch: () => Promise.reject(new Error('offline')) });
  await assert.doesNotReject(ctx.sidebarPreference.save('files', false));
});
