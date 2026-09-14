'use strict';

// The sidebar sections (pinned, group folders, tags) are toggled from the
// module preferences. sidebarCollapse() seeds each flag from the embedded
// prefs so the first bind already hides a section the user turned off, and
// follows every later toggle through the preferences-changed event.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function loadPrefs(embeddedPrefs) {
  const listeners = {};
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/preferences.js', {
    document: {
      getElementById: (id) =>
        id === 'file-prefs-data' && embeddedPrefs
          ? { textContent: JSON.stringify(embeddedPrefs) }
          : null,
      addEventListener: () => {},
    },
    addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); },
    dispatchEvent: (event) => { (listeners[event.type] || []).forEach((fn) => fn(event)); return true; },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
    matchMedia: () => ({ matches: false, addEventListener: () => {} }),
    sidebarPreference: { initial: () => false, save: () => {} },
    getCSRFToken: () => 'token',
    // Never let a save reach fetch: the test only cares about the flags.
    setTimeout: () => 0,
    clearTimeout: () => {},
  });
  return ctx;
}

test('every sidebar section is shown by default', () => {
  const sidebar = loadPrefs(null).sidebarCollapse();
  assert.equal(sidebar.showPinned, true);
  assert.equal(sidebar.showGroups, true);
  assert.equal(sidebar.showTags, true);
});

test('a saved preference hides its section from the first bind', () => {
  const sidebar = loadPrefs({ showTags: false, showGroups: false }).sidebarCollapse();
  assert.equal(sidebar.showPinned, true);
  assert.equal(sidebar.showGroups, false);
  assert.equal(sidebar.showTags, false);
});

test('toggling a section preference updates the sidebar', () => {
  const ctx = loadPrefs(null);
  const sidebar = ctx.sidebarCollapse();
  sidebar.init();
  const prefs = ctx.filePreferences();

  prefs.update('showTags', false);
  assert.equal(sidebar.showTags, false);
  prefs.update('showPinned', false);
  assert.equal(sidebar.showPinned, false);

  prefs.update('showTags', true);
  assert.equal(sidebar.showTags, true);
});
