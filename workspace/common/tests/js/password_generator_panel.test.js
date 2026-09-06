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
  const ctx = loadScripts([WORDLIST, GENERATOR], {
    crypto: globalThis.crypto,
    localStorage: storage,
  });
  const dispatched = [];
  const panel = ctx.passwordGeneratorPanel(overrides);
  panel.$watch = () => {};
  panel.$dispatch = (name, detail) => dispatched.push({ name, detail });
  return { panel, storage, dispatched };
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

test('the teardown drops the value the panel was holding', () => {
  // How a vault lock takes the generated password back: the host stops
  // rendering the panel, Alpine runs destroy(), the plaintext goes with it.
  const { panel } = makePanel();
  panel.init();
  assert.ok(panel.value);
  panel.destroy();
  assert.equal(panel.value, '');
});

test('the options are remembered, and nothing else is', () => {
  // A whitelist, not a search for the current value: the panel redraws on
  // every option change, so "the value on screen is not in storage" would
  // still hold with the previous one sitting there.
  const { panel, storage } = makePanel();
  panel.init();
  panel.length = 42;
  panel.persist();

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
  first.panel.persist();

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

test('applying hands the value to the host', () => {
  // Whether the panel then goes away is the host's call: it owns the x-if.
  const { panel, dispatched } = makePanel();
  panel.init();
  const drawn = panel.value;
  panel.apply();
  assert.deepEqual(
    dispatched.map((d) => d.name),
    ['password-apply']
  );
  assert.equal(dispatched[0].detail.value, drawn);
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
  panel.regenerate();
  assert.ok(Math.abs(panel.bits - 6 * Math.log2(1296)) < 1e-9);
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

test('every restored option is checked, not just taken', () => {
  // Storage is user-writable and outlives any version of this file. A mode of
  // 'foo' hides both option panes with neither tab active, a length of 999
  // draws 999 characters under a slider pinned at 64, and an empty separator
  // runs the words together under an entropy figure that assumes a boundary.
  const shared = fakeStorage();
  shared.setItem(
    'passwordGenerator.options',
    JSON.stringify({
      mode: 'foo',
      length: 999,
      words: 0,
      separator: '',
      capitalise: 'yes',
      upper: true,
    })
  );
  const { panel } = makePanel({}, shared);
  panel.init();
  assert.equal(panel.mode, 'password');
  assert.equal(panel.length, 20);
  assert.equal(panel.words, 6);
  assert.equal(panel.separator, '-');
  assert.equal(panel.capitalise, true);
  // A key that does pass is still honoured: this is a filter, not a reset.
  assert.equal(panel.upper, true);
});

test('a stored option at the edge of what the controls offer is kept', () => {
  const shared = fakeStorage();
  shared.setItem(
    'passwordGenerator.options',
    JSON.stringify({ mode: 'passphrase', length: 64, words: 12, separator: ' ' })
  );
  const { panel } = makePanel({}, shared);
  panel.init();
  assert.equal(panel.mode, 'passphrase');
  assert.equal(panel.length, 64);
  assert.equal(panel.words, 12);
  assert.equal(panel.separator, ' ');
  assert.equal(panel.value.split(' ').length, 12, panel.value);
});

test('Enter is stopped everywhere but on the buttons', () => {
  // The panel is embedded in the entry form, and Chromium submits implicitly
  // from a range and from a checkbox. Left alone, Enter on the length slider
  // saves the entry with the password the draft already held and tears this
  // panel down with the drawn one still in it.
  const { panel } = makePanel();
  panel.init();
  const press = (matches) => {
    let prevented = false;
    panel.blockImplicitSubmit({
      target: { closest: (selector) => (matches ? { selector } : null) },
      preventDefault: () => {
        prevented = true;
      },
    });
    return prevented;
  };
  assert.equal(press(false), true, 'Enter on a control reached the form');
  assert.equal(press(true), false, 'Enter on a button stopped activating it');
});
