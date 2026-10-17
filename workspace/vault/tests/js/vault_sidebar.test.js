// The sidebar's own scope. It exists so that bindings on Lucide icons - which
// the icon library replaces at boot - always have an x-data close enough to
// have been attached first.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

function sidebar({ initial = false, narrow = false } = {}) {
  const saved = [];
  const ctx = loadScript('workspace/vault/ui/static/vault/ui/js/vault_sidebar.js', {
    matchMedia: () => ({ matches: narrow, addEventListener() {} }),
    sidebarPreference: {
      initial: () => initial,
      save: (module, collapsed) => saved.push([module, collapsed]),
    },
  });
  const instance = ctx.vaultSidebar();
  instance.init();
  return { instance, saved };
}

test('a wide screen opens on the preference the server painted', () => {
  assert.equal(sidebar({ initial: false }).instance.collapsed, false);
  assert.equal(sidebar({ initial: true }).instance.collapsed, true);
});

test('a toggle is written back as the vault module setting', () => {
  // The server sizes the <aside> from this setting; localStorage would never
  // reach it, and the next page would paint the old width.
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
