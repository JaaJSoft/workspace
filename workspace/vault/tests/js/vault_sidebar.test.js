// The sidebar's own scope. It exists so that bindings on Lucide icons - which
// the icon library replaces at boot - always have an x-data close enough to
// have been attached first.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

function sidebar({ initial = false, narrow = false } = {}) {
  const saved = [];
  const listeners = [];
  const media = {
    matches: narrow,
    addEventListener(type, listener) { listeners.push(listener); },
  };
  // Resizing across the breakpoint, as the browser reports it.
  const resize = (toNarrow) => {
    media.matches = toNarrow;
    listeners.forEach((listener) => listener({ matches: toNarrow }));
  };
  const ctx = loadScript('workspace/vault/ui/static/vault/ui/js/vault_sidebar.js', {
    matchMedia: () => media,
    sidebarPreference: {
      initial: () => initial,
      save: (module, collapsed) => saved.push([module, collapsed]),
    },
  });
  const instance = ctx.vaultSidebar();
  instance.init();
  return { instance, saved, resize };
}

test('a wide screen opens on the preference the server painted', () => {
  assert.equal(sidebar({ initial: false }).instance.collapsed, false);
  assert.equal(sidebar({ initial: true }).instance.collapsed, true);
});

test('a toggle is written back as the vault module setting', () => {
  // The server sizes the <aside> from this setting, so it is what the next
  // page paints with.
  const { instance, saved } = sidebar();
  instance.toggleCollapse();
  assert.equal(instance.collapsed, true);
  instance.toggleCollapse();
  assert.equal(instance.collapsed, false);
  assert.deepEqual(saved.map((call) => Array.from(call)), [['vault', true], ['vault', false]]);
});

test('a narrow screen narrows the sidebar instead of taking it away', () => {
  // The drawer stays open at every width, so the rail is the shape that fits.
  assert.equal(sidebar({ initial: false, narrow: true }).instance.collapsed, true);
});

test('the control does nothing while the rail is the only shape that fits', () => {
  const { instance, saved } = sidebar({ narrow: true });
  instance.toggleCollapse();
  assert.equal(instance.collapsed, true);
  // And the narrow state is not written down as a preference: widening the
  // window must give back the sidebar the user actually chose.
  assert.equal(saved.length, 0);
});

test('widening the window again gives back the sidebar the user chose', () => {
  const { instance, resize } = sidebar({ initial: false });
  resize(true);
  assert.equal(instance.collapsed, true);
  resize(false);
  assert.equal(instance.collapsed, false);
});

test('a collapsed preference stays collapsed across a resize', () => {
  const { instance, resize } = sidebar({ initial: true });
  resize(true);
  resize(false);
  assert.equal(instance.collapsed, true);
});
