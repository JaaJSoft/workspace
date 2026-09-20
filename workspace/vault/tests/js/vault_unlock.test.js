// The unlock gate's state machine, against a stubbed session: which device
// state mounts the recovery-key field, when the section unfolds on its own,
// and what a failure does to a key the device remembered. The rule under
// test is that a remembered key is never put on screen by anything short of
// the one password failure that cannot tell it apart from a wrong password.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const STORED = 'STORED-KEY';

function gate(options = {}) {
  const calls = { forgotten: 0, unlocks: [] };
  const ctx = loadScript('workspace/vault/ui/static/vault/ui/js/vault_unlock.js', {
    vaultSession: {
      rememberedSecret: () => options.remembered || null,
      onLock() {},
      onTick() {},
      watchForIdle() {},
      secondsUntilLock: () => 0,
      forgetDevice() { calls.forgotten += 1; },
      unlock: async (args) => {
        calls.unlocks.push(args);
        if (options.fail) {
          const err = new Error(options.fail);
          err.reason = options.fail;
          throw err;
        }
      },
    },
  });
  const component = ctx.vaultUnlockMixin();
  component.afterUnlock = async () => {};
  component.onLocked = () => {};
  component.confirm = async () => options.confirmed !== false;
  component.initUnlock();
  return { component, calls };
}

test('a fresh device asks for the key with the section open', () => {
  const { component } = gate();
  assert.equal(component.secretRequired, true);
  assert.equal(component.secretRemembered, false);
  assert.equal(component.secretPanelOpen, true);
  assert.equal(component.secretMissing(), true);
});

test('a device that remembers the key keeps the field unmounted and the section folded', () => {
  const { component } = gate({ remembered: STORED });
  assert.equal(component.secretText, STORED);
  assert.equal(component.remember, true);
  assert.equal(component.secretRequired, false);
  assert.equal(component.secretRemembered, true);
  assert.equal(component.secretPanelOpen, false);
  assert.equal(component.secretMissing(), false);
});

test('an unreadable remembered key forgets the device and opens the section empty', async () => {
  const { component, calls } = gate({ remembered: STORED, fail: 'recovery-key' });
  component.password = 'pw';
  await component.unlock();
  assert.equal(calls.forgotten, 1);
  assert.equal(component.secretText, '');
  assert.equal(component.secretRequired, true);
  assert.equal(component.secretRemembered, false);
  assert.equal(component.secretPanelOpen, true);
  assert.match(component.error, /could not be read/);
});

test('a password failure with a remembered key puts that key back on screen', async () => {
  const { component, calls } = gate({ remembered: STORED, fail: 'password' });
  component.password = 'pw';
  await component.unlock();
  assert.equal(calls.forgotten, 0);
  assert.equal(component.secretText, STORED);
  assert.equal(component.secretRequired, true);
  assert.equal(component.secretPanelOpen, true);
  assert.match(component.error, /belongs to another account/);
});

test('a password failure on a fresh device blames the password alone', async () => {
  const { component } = gate({ fail: 'password' });
  component.password = 'pw';
  component.secretText = 'TYPED';
  await component.unlock();
  assert.equal(component.secretRemembered, false);
  assert.match(component.error, /does not open this account/);
  assert.doesNotMatch(component.error, /another account/);
});

test('replacing a remembered key mounts an empty field the gate covers', () => {
  const { component, calls } = gate({ remembered: STORED });
  component.replaceSecret();
  assert.equal(component.secretText, '');
  assert.equal(component.secretRequired, true);
  assert.equal(component.secretRemembered, false);
  assert.equal(component.secretPanelOpen, true);
  assert.equal(component.secretMissing(), true);
  // Replace is reversible by a reload: the stored value is left alone.
  assert.equal(calls.forgotten, 0);
});

test('forgetting the key drops it from the device once confirmed', async () => {
  const { component, calls } = gate({ remembered: STORED });
  await component.forgetSecret();
  assert.equal(calls.forgotten, 1);
  assert.equal(component.secretText, '');
  assert.equal(component.secretRequired, true);
  assert.equal(component.remember, false);
  assert.equal(component.secretPanelOpen, true);
});

test('declining the confirmation leaves the remembered key alone', async () => {
  const { component, calls } = gate({ remembered: STORED, confirmed: false });
  await component.forgetSecret();
  assert.equal(calls.forgotten, 0);
  assert.equal(component.secretText, STORED);
  assert.equal(component.secretRequired, false);
  assert.equal(component.remember, true);
});
