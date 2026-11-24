'use strict';

// The collapsed preference is a desktop one. Below `lg` the Files drawer
// opens over the page as the full sidebar, never as the 64px icon rail, and
// coming back to a desktop width restores what the user chose there.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function load({ mobile, preferCollapsed }) {
  const media = { matches: mobile, listeners: [] };
  media.addEventListener = (type, fn) => media.listeners.push(fn);
  const saved = [];
  const drawer = { checked: true };
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/preferences.js', {
    document: {
      getElementById: (id) => (id === 'files-drawer' ? drawer : null),
      addEventListener: () => {},
    },
    addEventListener: () => {},
    matchMedia: () => media,
    sidebarPreference: { initial: () => preferCollapsed, save: (module, value) => saved.push([module, value]) },
    getCSRFToken: () => 'token',
    setTimeout: () => 0,
    clearTimeout: () => {},
  });
  const sidebar = ctx.sidebarCollapse();
  sidebar.init();
  const resize = (toMobile) => {
    media.matches = toMobile;
    media.listeners.forEach((fn) => fn({ matches: toMobile }));
  };
  return { sidebar, resize, saved, drawer };
}

test('a phone gets the full sidebar whatever the desktop preference', () => {
  const { sidebar } = load({ mobile: true, preferCollapsed: true });
  assert.equal(sidebar.collapsed, false);
});

test('a desktop starts from the stored preference', () => {
  assert.equal(load({ mobile: false, preferCollapsed: true }).sidebar.collapsed, true);
  assert.equal(load({ mobile: false, preferCollapsed: false }).sidebar.collapsed, false);
});

test('crossing the breakpoint restores the desktop preference', () => {
  const { sidebar, resize } = load({ mobile: false, preferCollapsed: true });
  resize(true);
  assert.equal(sidebar.collapsed, false);
  resize(false);
  assert.equal(sidebar.collapsed, true);
});

test('the toggle is a no-op on a phone and saved on a desktop', () => {
  const phone = load({ mobile: true, preferCollapsed: false });
  phone.sidebar.toggleCollapse();
  assert.equal(phone.sidebar.collapsed, false);
  assert.deepEqual(phone.saved, []);

  const desk = load({ mobile: false, preferCollapsed: false });
  desk.sidebar.toggleCollapse();
  assert.equal(desk.sidebar.collapsed, true);
  assert.deepEqual(desk.saved.map((entry) => Array.from(entry)), [['files', true]]);
  desk.resize(true);
  desk.resize(false);
  assert.equal(desk.sidebar.collapsed, true);
});

test('navigating closes the drawer on a phone, not on a desktop', () => {
  const phone = load({ mobile: true, preferCollapsed: false });
  phone.drawer.checked = true;
  phone.sidebar.syncActiveView();
  assert.equal(phone.drawer.checked, false);

  const desk = load({ mobile: false, preferCollapsed: false });
  desk.drawer.checked = true;
  desk.sidebar.syncActiveView();
  assert.equal(desk.drawer.checked, true);
});
