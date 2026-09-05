// What the vault adds around the shared generator: which field's panel is
// open, where a generated value lands, and - the part that matters - that a
// lock takes the value back the way it takes every other plaintext back.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const MIXIN = 'workspace/vault/ui/static/vault/ui/js/vault_generator.js';

function mixin() {
  const copied = [];
  const dispatched = [];
  const ctx = loadScript(MIXIN, {
    CustomEvent: class {
      constructor(name) {
        this.type = name;
      }
    },
    dispatchEvent: (event) => dispatched.push(event.type),
    vaultClipboard: {
      copy: async (label, value, options) => copied.push({ label, value, options }),
    },
    vaultCrypto: {
      randomBytes: (count) => new Uint8Array(count),
    },
  });
  return { component: ctx.vaultGeneratorMixin(), copied, dispatched };
}

test('opening a field panel closes the one that was open', () => {
  // Two open panels would show two different passwords for two fields of the
  // same entry, and only one of them can be applied.
  const { component } = mixin();
  component.openGenerator('password');
  assert.equal(component.generatorField, 'password');
  component.openGenerator('other');
  assert.equal(component.generatorField, 'other');
  component.openGenerator('other');
  assert.equal(component.generatorField, null);
});

test('a generated value lands in the draft field that asked for it', () => {
  const { component } = mixin();
  component.draft = { values: { password: 'old' } };
  component.openGenerator('password');
  component.applyGenerated('password', 'drawn');
  assert.equal(component.draft.values.password, 'drawn');
  assert.equal(component.generatorField, null);
});

test('applying with no draft open changes nothing and throws nothing', () => {
  // The dialog can close under the panel - a lock does exactly that.
  const { component } = mixin();
  component.draft = null;
  component.applyGenerated('password', 'drawn');
  assert.equal(component.generatorField, null);
});

test('copying a generated password goes through the clearing clipboard', () => {
  // Not navigator.clipboard directly: a generated password left on the
  // clipboard outlives the page that drew it.
  const { component, copied } = mixin();
  component.copyGenerated('drawn');
  assert.equal(copied.length, 1);
  assert.equal(copied[0].value, 'drawn');
  assert.equal(copied[0].options.transient, true);
});

test('clearing asks every open panel to drop what it holds', () => {
  const { component, dispatched } = mixin();
  component.openGenerator('password');
  component.clearGenerators();
  assert.deepEqual(dispatched, ['password-generator-clear']);
  assert.equal(component.generatorField, null);
  assert.equal(component.generatorOpen, false);
});

test('the byte source handed to the panel is the vault bundle, not the page', () => {
  // The module's randomness is audited inside vault-crypto.js; a password
  // drawn anywhere else would sit outside everything that guards it.
  const drawn = [];
  const ctx = loadScript(MIXIN, {
    vaultCrypto: {
      randomBytes: (count) => {
        drawn.push(count);
        return new Uint8Array(count);
      },
    },
  });
  const component = ctx.vaultGeneratorMixin();
  component.generatorDeps().randomBytes(7);
  assert.deepEqual(drawn, [7]);
});
