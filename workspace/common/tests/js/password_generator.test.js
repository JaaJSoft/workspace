// The generator's two promises are that the output is uniform and that it
// carries what was asked for. Neither is visible by reading a password, so
// both are pinned here.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript, loadScripts } = require('./loader');

const GENERATOR = 'workspace/common/static/ui/js/password_generator.js';
const WORDLIST = 'workspace/common/static/ui/js/password_wordlist.js';

const BROWSER = { crypto: globalThis.crypto };

function load(extraGlobals = BROWSER) {
  return loadScripts([WORDLIST, GENERATOR], extraGlobals).passwordGenerator;
}

/** A byte source cycling 0..255 forever, the shape that exposes modulo bias. */
function cyclingBytes() {
  let next = 0;
  return (count) => {
    const out = new Uint8Array(count);
    for (let i = 0; i < count; i += 1) {
      out[i] = next;
      next = (next + 1) % 256;
    }
    return out;
  };
}

// ---------------------------------------------------------------- randomInt

test('randomInt draws every value equally often, rejecting the biased tail', () => {
  // 256 is not a multiple of 62, so a naive `byte % 62` hands indices 0-7 one
  // extra chance per cycle. Rejection sampling drops bytes 248-255 instead,
  // which is what makes this histogram flat: swap the rejection for a modulo
  // and indices 0-7 come out at 24 against 19 for the tail.
  const G = load();
  const draw = cyclingBytes();
  const counts = new Array(62).fill(0);
  for (let i = 0; i < 62 * 20; i += 1) counts[G.randomInt(62, draw)] += 1;
  assert.deepEqual(Array.from(new Set(counts)), [20]);
});

test('randomInt stays inside the range it was given', () => {
  const G = load();
  for (let i = 0; i < 2000; i += 1) {
    const value = G.randomInt(94, G.secureRandomBytes);
    assert.ok(value >= 0 && value < 94, `${value} out of range`);
  }
});

test('randomInt reaches past a single byte when the range needs it', () => {
  // A one-byte implementation caps at 255 and would silently draw the first
  // fifth of the wordlist for every word of every passphrase.
  const G = load();
  let highest = 0;
  for (let i = 0; i < 4000; i += 1) {
    highest = Math.max(highest, G.randomInt(1296, G.secureRandomBytes));
  }
  assert.ok(highest > 1000, `never drew above ${highest}`);
});

test('the default byte source refuses to run without a CSPRNG', () => {
  // Not a fallback: a browser with no getRandomValues gets an error, never a
  // weaker password it cannot tell apart from a strong one.
  const G = loadScript(GENERATOR, { crypto: undefined }).passwordGenerator;
  assert.throws(() => G.secureRandomBytes(8), /CSPRNG/);
});

// ---------------------------------------------------------- generatePassword

const ALL_CLASSES = { upper: true, lower: true, digits: true, symbols: true };

test('a generated password is as long as it was asked to be', () => {
  const G = load();
  for (const length of [8, 16, 64]) {
    assert.equal(G.generatePassword({ length, ...ALL_CLASSES }).length, length);
  }
});

test('every requested class shows up in the output', () => {
  const G = load();
  for (let i = 0; i < 200; i += 1) {
    const value = G.generatePassword({ length: 8, ...ALL_CLASSES });
    assert.match(value, /[A-Z]/, value);
    assert.match(value, /[a-z]/, value);
    assert.match(value, /[0-9]/, value);
    assert.match(value, new RegExp(`[${G.SYMBOLS.replace(/./g, '\\$&')}]`), value);
  }
});

test('an excluded class never shows up in the output', () => {
  const G = load();
  for (let i = 0; i < 200; i += 1) {
    const value = G.generatePassword({ length: 20, lower: true, digits: true });
    assert.doesNotMatch(value, /[^a-z0-9]/, value);
  }
});

test('look-alike characters are gone when they are excluded', () => {
  const G = load();
  for (let i = 0; i < 100; i += 1) {
    const value = G.generatePassword({
      length: 32,
      ...ALL_CLASSES,
      avoidLookalikes: true,
    });
    assert.doesNotMatch(value, /[lI1O0]/, value);
  }
});

test('asking for no class at all is refused rather than answered', () => {
  const G = load();
  assert.throws(() => G.generatePassword({ length: 12 }), /at least one/);
});

test('a length that cannot hold every requested class is refused', () => {
  // Three classes never fit in two characters, and the class-presence retry
  // would otherwise spin until the iteration cap.
  const G = load();
  assert.throws(
    () => G.generatePassword({ length: 2, upper: true, lower: true, digits: true }),
    /too short/
  );
});

test('an exclusion that would empty a requested class is refused', () => {
  // No class empties under the real catalogue; this guards the day someone
  // widens it. Without it the class-presence retry could never succeed.
  const G = load();
  assert.throws(
    () =>
      G.generatePassword({
        length: 12,
        digits: true,
        lower: true,
        avoidLookalikes: true,
        lookalikes: '0123456789',
      }),
    /leaves no/
  );
});

// -------------------------------------------------------- generatePassphrase

test('a passphrase carries the number of words it was asked for', () => {
  const G = load();
  const value = G.generatePassphrase({ words: 6, separator: '-' });
  assert.equal(value.split('-').length >= 6, true);
  assert.equal(G.generatePassphrase({ words: 4, separator: ' ' }).split(' ').length, 4);
});

test('a passphrase only uses words from the list', () => {
  const G = load();
  const known = new Set(Array.from(G.defaultWordlist()));
  for (const word of G.generatePassphrase({ words: 8, separator: ' ' }).split(' ')) {
    assert.ok(known.has(word), `${word} is not in the list`);
  }
});

test('capitalising a passphrase leaves the words themselves alone', () => {
  const G = load();
  const value = G.generatePassphrase({ words: 5, separator: ' ', capitalise: true });
  const known = new Set(Array.from(G.defaultWordlist()));
  for (const word of value.split(' ')) {
    assert.match(word, /^[A-Z]/, value);
    assert.ok(known.has(word.toLowerCase()), `${word} is not in the list`);
  }
});

// ------------------------------------------------------------- entropyBits

test('password entropy matches an exhaustive count of the valid strings', () => {
  // The independent oracle: enumerate every string of length 3 over the
  // digits-and-symbols alphabet and count the ones carrying both classes.
  // An implementation reporting length x log2(alphabet) - the usual shortcut -
  // overstates this by a quarter of a bit and fails here.
  const G = load();
  const opts = { length: 3, digits: true, symbols: true };
  const alphabet = G.DIGITS + G.SYMBOLS;
  let valid = 0;
  for (const a of alphabet) {
    for (const b of alphabet) {
      for (const c of alphabet) {
        const s = a + b + c;
        if (/[0-9]/.test(s) && [...s].some((ch) => G.SYMBOLS.includes(ch))) valid += 1;
      }
    }
  }
  assert.ok(Math.abs(G.entropyBits(opts) - Math.log2(valid)) < 1e-9);
});

test('passphrase entropy is the wordlist, and capitalising adds nothing to it', () => {
  // Capitalising every word is deterministic: it changes what the phrase looks
  // like and not how many phrases there are.
  const G = load();
  const plain = G.entropyBits({ mode: 'passphrase', words: 6 });
  assert.ok(Math.abs(plain - 6 * Math.log2(1296)) < 1e-9);
  assert.equal(G.entropyBits({ mode: 'passphrase', words: 6, capitalise: true }), plain);
  assert.equal(G.entropyBits({ mode: 'passphrase', words: 6, separator: '@' }), plain);
});

// ------------------------------------------------------------- the source

test('the generator draws from one place, and that place is the CSPRNG', () => {
  // Behaviour tests can only observe the source that ran. This reads the file
  // instead, so a `Math.random` fallback added for a browser that "does not
  // support" getRandomValues cannot hide behind a branch no test takes.
  const fs = require('node:fs');
  const path = require('node:path');
  // Comments are stripped first, so the file stays free to name the thing it
  // refuses to do - the header does exactly that.
  const source = fs
    .readFileSync(path.join(__dirname, '..', '..', '..', '..', GENERATOR), 'utf8')
    .replace(/\/\*[\s\S]*?\*\/|^[ \t]*\/\/[^\n]*/gm, '');
  assert.doesNotMatch(source, /Math\.random/, 'Math.random in the generator');
  assert.equal(
    (source.match(/getRandomValues\(/g) || []).length,
    1,
    'the CSPRNG should be called from exactly one place'
  );
});
