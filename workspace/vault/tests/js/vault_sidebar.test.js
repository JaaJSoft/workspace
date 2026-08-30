// The sidebar's own scope. It exists so that bindings on Lucide icons - which
// the icon library replaces at boot - always have an x-data close enough to
// have been attached first.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

function sidebar(store, narrow = false) {
  const ctx = loadScript('workspace/vault/ui/static/vault/ui/js/vault_sidebar.js', {
    localStorage: store,
    matchMedia: () => ({ matches: narrow, addEventListener() {} }),
  });
  return ctx.vaultSidebar();
}

function memory() {
  return {
    values: {},
    getItem(key) {
      return Object.prototype.hasOwnProperty.call(this.values, key) ? this.values[key] : null;
    },
    setItem(key, value) { this.values[key] = String(value); },
  };
}

test('the collapse survives a reload', () => {
  const store = memory();
  const first = sidebar(store);
  first.init();
  assert.equal(first.collapsed, false);
  first.toggleCollapse();
  assert.equal(first.collapsed, true);

  const second = sidebar(store);
  second.init();
  assert.equal(second.collapsed, true);
});

test('the two vault screens share one key', () => {
  // Collapsing on the listing and finding it expanded inside a vault would
  // read as the setting not having been kept.
  const store = memory();
  const instance = sidebar(store);
  instance.init();
  instance.toggleCollapse();
  assert.equal(store.values['vault.sidebar.collapsed'], 'true');
});

test('storage that refuses to be read leaves the sidebar open, not broken', () => {
  const denied = {
    getItem() { throw new Error('denied'); },
    setItem() { throw new Error('denied'); },
  };
  const instance = sidebar(denied);
  instance.init();
  assert.equal(instance.collapsed, false);
  instance.toggleCollapse();
  assert.equal(instance.collapsed, true);
});

test('a narrow screen narrows the sidebar instead of taking it away', () => {
  // The drawer stays open at every width, so the rail is the shape that fits.
  const instance = sidebar(memory(), true);
  instance.init();
  assert.equal(instance.collapsed, true);
});

test('the control does nothing while the rail is the only shape that fits', () => {
  const store = memory();
  const instance = sidebar(store, true);
  instance.init();
  instance.toggleCollapse();
  assert.equal(instance.collapsed, true);
  // And the narrow state is not written down as a preference: widening the
  // window must give back the sidebar the user actually chose.
  assert.equal(store.values['vault.sidebar.collapsed'], undefined);
});

test('a wide screen restores what was chosen', () => {
  const store = memory();
  const wide = sidebar(store, false);
  wide.init();
  wide.toggleCollapse();
  assert.equal(store.values['vault.sidebar.collapsed'], 'true');

  const narrow = sidebar(store, true);
  narrow.init();
  assert.equal(narrow.collapsed, true);
});
