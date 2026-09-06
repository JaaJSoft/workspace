// What the vault adds around the shared generator: which field's panel is
// open, where a generated value lands, and - the part that matters - that a
// lock takes the value back the way it takes every other plaintext back.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const MIXIN = 'workspace/vault/ui/static/vault/ui/js/vault_generator.js';

function mixin() {
  const copied = [];
  const ctx = loadScript(MIXIN, {
    vaultClipboard: {
      copy: async (label, value, options) => copied.push({ label, value, options }),
    },
    vaultCrypto: {
      randomBytes: (count) => new Uint8Array(count),
    },
  });
  return { component: ctx.vaultGeneratorMixin(), copied };
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

test('clearing takes both panels off the screen', () => {
  // Both are mounted under x-if on these two flags, so dropping them is what
  // tears the panels down and runs the destroy() that lets go of the value.
  const { component } = mixin();
  component.openGenerator('password');
  component.openGeneratorDialog();
  component.generatorError = 'stale';
  component.clearGenerators();
  assert.equal(component.generatorField, null);
  assert.equal(component.generatorOpen, false);
  assert.equal(component.generatorError, '');
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

test('a refused clipboard says so beside the panel, not behind the backdrop', () => {
  // Copying is the only way the value leaves the dialog, and closing the
  // dialog drops it: a rejected write nobody catches leaves the user believing
  // they hold a password they never got. It goes in generatorError rather than
  // error because the page-level alert renders in <main>, under the modal.
  const ctx = loadScript(MIXIN, {
    vaultClipboard: { copy: async () => { throw new Error('denied'); } },
    vaultCrypto: { randomBytes: (count) => new Uint8Array(count) },
  });
  const component = ctx.vaultGeneratorMixin();
  component.error = '';
  return component.copyGenerated('drawn').then(() => {
    assert.match(component.generatorError, /could not be copied/);
    assert.equal(component.error, '');
  });
});

test('a copy that went through clears the message the last one left', () => {
  const { component } = mixin();
  component.generatorError = 'That value could not be copied.';
  return component.copyGenerated('drawn').then(() => {
    assert.equal(component.generatorError, '');
  });
});
