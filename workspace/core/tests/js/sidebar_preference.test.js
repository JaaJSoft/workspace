'use strict';

// The collapsed-sidebar preference the shell embeds on every module page and
// the components write back through the settings API.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('../../../common/tests/js/loader');

const SCRIPTS = ['workspace/core/static/core/js/user_settings.js', 'workspace/core/static/core/js/sidebar_preference.js'];

function load({ embedded, fetch } = {}) {
  const document = {
    getElementById: (id) =>
      id === 'sidebar-collapsed-data' && embedded !== undefined ? { textContent: embedded } : null,
  };
  return loadScripts(SCRIPTS, { document, fetch, getCSRFToken: () => 'csrf-token' });
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

function loadDrawer({ embedded = 'false', narrow = false } = {}) {
  const saved = [];
  const queries = [];
  const ctx = loadScripts(['workspace/core/static/core/js/sidebar_preference.js'], {
    document: { getElementById: () => ({ textContent: embedded }) },
    saveUserSetting: (module, key, value) => { saved.push([module, key, value]); return Promise.resolve(); },
    matchMedia: (query) => { queries.push(query); return { matches: narrow }; },
  });
  return { drawer: { ...ctx.sidebarPreference.drawerMixin('mail') }, saved, queries };
}

test('drawerMixin() starts from the preference the shell rendered', () => {
  assert.equal(loadDrawer({ embedded: 'true' }).drawer.collapsed, true);
  assert.equal(loadDrawer({ embedded: 'false' }).drawer.collapsed, false);
});

test('drawerMixin() collapses on desktop only', () => {
  const { drawer, queries } = loadDrawer({ embedded: 'true' });
  assert.equal(drawer.sidebarCollapsed(), true);
  assert.deepEqual([...queries], ['(max-width: 1023px)']);
  assert.equal(loadDrawer({ embedded: 'true', narrow: true }).drawer.sidebarCollapsed(), false);
});

test('drawerMixin() toggles and saves the module preference on desktop', () => {
  const { drawer, saved } = loadDrawer();
  drawer.toggleCollapse();
  assert.equal(drawer.collapsed, true);
  assert.deepEqual(saved.map((s) => [...s]), [['mail', 'sidebar_collapsed', true]]);
});

test('drawerMixin() leaves the preference alone below lg', () => {
  const { drawer, saved } = loadDrawer({ narrow: true });
  drawer.toggleCollapse();
  assert.equal(drawer.collapsed, false);
  assert.equal(saved.length, 0);
});
