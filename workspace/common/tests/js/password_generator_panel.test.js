// The panel around the generator. What matters here is not the drawing - that
// is pinned in password_generator.test.js - but what the panel does with the
// value it is holding: where it may keep it, and when it has to let go.
const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('./loader');

const GENERATOR = 'workspace/common/static/ui/js/password_generator.js';
const WORDLIST = 'workspace/common/static/ui/js/password_wordlist.js';

function fakeStorage() {
  const entries = new Map();
  return {
    entries,
    getItem: (key) => (entries.has(key) ? entries.get(key) : null),
    setItem: (key, value) => entries.set(key, String(value)),
    removeItem: (key) => entries.delete(key),
  };
}

function makePanel(overrides = {}, storage = fakeStorage()) {
  const listeners = [];
  const ctx = loadScripts([WORDLIST, GENERATOR], {
    crypto: globalThis.crypto,
    localStorage: storage,
    addEventListener: (name, handler) => listeners.push({ name, handler }),
    removeEventListener: (name, handler) => {
      const at = listeners.findIndex((l) => l.name === name && l.handler === handler);
      if (at >= 0) listeners.splice(at, 1);
    },
  });
  const dispatched = [];
  const panel = ctx.passwordGeneratorPanel(overrides);
  panel.$watch = () => {};
  panel.$dispatch = (name, detail) => dispatched.push({ name, detail });
  return { panel, storage, listeners, dispatched };
}

test('the panel opens with a password already drawn', () => {
  const { panel } = makePanel();
  panel.init();
  assert.equal(panel.value.length, panel.length);
});

test('clearing takes the value away', () => {
  const { panel } = makePanel();
  panel.init();
  panel.clear();
  assert.equal(panel.value, '');
});

test('the clear event reaches the panel, and stops reaching it once torn down', () => {
  // This is how a vault lock takes the generated password back: the host
  // dispatches, the panel drops what it holds. A listener that survived the
  // teardown would pile up one more handler per open.
  const { panel, listeners } = makePanel();
  panel.init();
  const registered = listeners.filter((l) => l.name === 'password-generator-clear');
  assert.equal(registered.length, 1);

  registered[0].handler();
  assert.equal(panel.value, '');

  panel.destroy();
  assert.equal(listeners.filter((l) => l.name === 'password-generator-clear').length, 0);
});

test('the options are remembered, and nothing else is', () => {
  // A whitelist, not a search for the current value: optionsChanged() draws a
  // fresh password right after writing, so "the value on screen is not in
  // storage" would still hold with the previous one sitting there.
  const { panel, storage } = makePanel();
  panel.init();
  panel.length = 42;
  panel.optionsChanged();

  assert.deepEqual(Array.from(storage.entries.keys()), ['passwordGenerator.options']);
  const stored = JSON.parse(storage.entries.get('passwordGenerator.options'));
  assert.deepEqual(Object.keys(stored).sort(), [
    'avoidLookalikes',
    'capitalise',
    'digits',
    'length',
    'lower',
    'mode',
    'separator',
    'symbols',
    'upper',
    'words',
  ]);
  assert.equal(stored.length, 42);
});

test('remembered options come back on the next open', () => {
  const shared = fakeStorage();
  const first = makePanel({}, shared);
  first.panel.init();
  first.panel.length = 37;
  first.panel.mode = 'passphrase';
  first.panel.optionsChanged();

  const second = makePanel({}, shared);
  second.panel.init();
  assert.equal(second.panel.length, 37);
  assert.equal(second.panel.mode, 'passphrase');
});

test('an impossible request shows an error instead of a stale password', () => {
  // Unticking the last character class must not leave the previous password
  // on screen looking like the answer to the new options.
  const { panel } = makePanel();
  panel.init();
  panel.upper = false;
  panel.lower = false;
  panel.digits = false;
  panel.symbols = false;
  panel.regenerate();
  assert.equal(panel.value, '');
  assert.match(panel.error, /at least one/);
});

test('a recovered request clears the error', () => {
  const { panel } = makePanel();
  panel.init();
  Object.assign(panel, { upper: false, lower: false, digits: false, symbols: false });
  panel.regenerate();
  panel.lower = true;
  panel.regenerate();
  assert.equal(panel.error, '');
  assert.ok(panel.value);
});

test('applying hands the value to the host and closes', () => {
  const { panel, dispatched } = makePanel();
  panel.init();
  const drawn = panel.value;
  panel.open = true;
  panel.apply();
  assert.deepEqual(
    dispatched.map((d) => d.name),
    ['password-apply']
  );
  assert.equal(dispatched[0].detail.value, drawn);
  assert.equal(panel.open, false);
});

test('copying asks the host to copy rather than touching the clipboard itself', () => {
  // The host owns the clipboard: in a vault it is the one with the clearing
  // timer, elsewhere it is the plain browser one.
  const { panel, dispatched } = makePanel();
  panel.init();
  panel.copy();
  assert.equal(dispatched[0].name, 'password-copy');
  assert.equal(dispatched[0].detail.value, panel.value);
});

test('the panel reports the entropy of what it drew', () => {
  const { panel } = makePanel();
  panel.init();
  panel.mode = 'passphrase';
  panel.words = 6;
  assert.ok(Math.abs(panel.entropy() - 6 * Math.log2(1296)) < 1e-9);
});

test('the panel draws through the byte source it was handed', () => {
  // How the vault keeps its own randomness inside the bundle it audits.
  let calls = 0;
  const { panel } = makePanel({
    randomBytes: (count) => {
      calls += 1;
      return globalThis.crypto.getRandomValues(new Uint8Array(count));
    },
  });
  panel.init();
  assert.ok(calls > 0, 'the injected source was never used');
});

test('a separator outside the catalogue is replaced, a listed one is kept', () => {
  const { panel } = makePanel();
  panel.init();
  panel.separator = '';
  panel.normaliseSeparator();
  assert.equal(panel.separator, '-');

  panel.separator = '§';
  panel.normaliseSeparator();
  assert.equal(panel.separator, '-');

  panel.separator = '.';
  panel.normaliseSeparator();
  assert.equal(panel.separator, '.');
});

test('options saved before the picker was a closed set are repaired on open', () => {
  // The hole a blur handler alone left: an empty separator persisted by an
  // older version comes straight back out of storage and is used to draw
  // before anything the user does could fix it.
  const shared = fakeStorage();
  shared.setItem(
    'passwordGenerator.options',
    JSON.stringify({ mode: 'passphrase', words: 5, separator: '' })
  );
  const { panel } = makePanel({}, shared);
  panel.init();
  assert.equal(panel.separator, '-');
  assert.ok(panel.value.includes('-'), panel.value);
});
