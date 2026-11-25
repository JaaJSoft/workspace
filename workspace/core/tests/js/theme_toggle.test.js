'use strict';

// The navbar's light/dark switch: it flips <html data-theme> at once and
// stores the theme, putting the previous one back when the write fails.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('../../../common/tests/js/loader');

function boot({ theme = 'light', reply = { ok: true, status: 200 } } = {}) {
  const attributes = { 'data-theme': theme, 'data-light-theme': 'light', 'data-dark-theme': 'dracula' };
  const html = {
    getAttribute: (name) => attributes[name] ?? null,
    setAttribute: (name, value) => { attributes[name] = value; },
  };
  const toggle = { checked: false, listeners: {} };
  toggle.addEventListener = (type, fn) => { toggle.listeners[type] = fn; };
  const calls = [];
  loadScripts(['workspace/core/static/core/js/user_settings.js', 'workspace/core/static/core/js/theme_toggle.js'], {
    document: { documentElement: html, getElementById: (id) => (id === 'themeToggle' ? toggle : null) },
    MutationObserver: class { observe() {} },
    getCSRFToken: () => 'token',
    fetch: (url, options) => {
      calls.push({ url, options });
      return reply instanceof Error ? Promise.reject(reply) : Promise.resolve(reply);
    },
  });
  const flip = async (checked) => {
    toggle.checked = checked;
    toggle.listeners.change.call(toggle);
    await new Promise((resolve) => setImmediate(resolve));
  };
  return { attributes, calls, flip };
}

test('switching to dark stores the dark theme', async () => {
  const { attributes, calls, flip } = boot();
  await flip(true);

  assert.equal(attributes['data-theme'], 'dracula');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/v1/settings/core/theme');
  assert.equal(calls[0].options.method, 'PUT');
  assert.deepEqual(JSON.parse(calls[0].options.body), { value: 'dracula' });
});

test('a refused theme puts the previous one back', async () => {
  const { attributes, flip } = boot({ reply: { ok: false, status: 400 } });
  await flip(true);
  assert.equal(attributes['data-theme'], 'light');
});

test('a theme lost to the network puts the previous one back', async () => {
  const { attributes, flip } = boot({ reply: new TypeError('offline') });
  await flip(true);
  assert.equal(attributes['data-theme'], 'light');
});
