// The wordlist is a security parameter, not data: "10.34 bits per word" is
// exactly log2(1296), and it stops being true the moment the list holds 1295
// entries or names one of them twice. Nothing at runtime would notice - a
// passphrase built from a shortened list looks exactly like one built from
// the whole - so the count and the uniqueness are checked here instead.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('./loader');

const WORDLIST = 'workspace/common/static/ui/js/password_wordlist.js';

test('the list carries exactly 1296 words', () => {
  const ctx = loadScript(WORDLIST);
  assert.equal(ctx.PASSWORD_WORDLIST.length, 1296);
});

test('no word appears twice', () => {
  const ctx = loadScript(WORDLIST);
  const words = Array.from(ctx.PASSWORD_WORDLIST);
  assert.equal(new Set(words).size, words.length);
});

test('every word is lowercase and short enough to type', () => {
  // The EFF short list is built for typing: three to five letters. `yo-yo` is
  // the one hyphenated entry, which is why the pattern is not [a-z] alone -
  // it also means a passphrase joined with "-" can read as one word too many.
  const ctx = loadScript(WORDLIST);
  const offenders = Array.from(ctx.PASSWORD_WORDLIST).filter(
    (word) => !/^[a-z-]{3,5}$/.test(word)
  );
  assert.deepEqual(offenders, []);
});
