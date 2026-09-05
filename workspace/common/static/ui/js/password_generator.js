// Password and passphrase generation, for any form that needs a strong value.
//
// The byte source is a parameter rather than a hard-wired call. Callers that
// hold their own audited CSPRNG wrapper pass it in and keep their randomness
// inside the code they audit; everyone else gets `secureRandomBytes` below.
// There is no third option: nothing here ever falls back to Math.random, and
// a browser without crypto.getRandomValues gets an error rather than a
// password it could not tell apart from a strong one.
//
// The wordlist lives in password_wordlist.js so a caller that only wants
// random characters does not load 7 KB of words it never reads.

const UPPER = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
const LOWER = 'abcdefghijklmnopqrstuvwxyz';
const DIGITS = '0123456789';
const SYMBOLS = '!@#$%^&*()-_=+[]{};:,.?';
// The characters a person mistypes when reading a password off a screen or a
// printed sheet. Excluding them costs about a tenth of a bit per character.
const LOOKALIKES = 'lI1O0';

// A class-presence retry cannot fail forever once the alphabet is valid and
// long enough, so reaching this means an invariant broke - loudly, rather
// than as a frozen tab.
const MAX_DRAWS = 10000;

const CLASS_IDS = ['upper', 'lower', 'digits', 'symbols'];
const CLASS_CHARS = { upper: UPPER, lower: LOWER, digits: DIGITS, symbols: SYMBOLS };

function secureRandomBytes(count) {
  const source = typeof crypto === 'undefined' ? null : crypto;
  if (!source || typeof source.getRandomValues !== 'function') {
    throw new Error('no CSPRNG: this browser has no crypto.getRandomValues');
  }
  return source.getRandomValues(new Uint8Array(count));
}

function defaultWordlist() {
  const words = typeof window === 'undefined' ? null : window.PASSWORD_WORDLIST;
  if (!words || !words.length) {
    throw new Error('no wordlist: load password_wordlist.js before generating a passphrase');
  }
  return words;
}

// Uniform in [0, max). Rejection sampling, never a modulo of a raw byte: 256
// is not a multiple of most alphabet sizes, so `byte % 62` would hand the
// first eight characters an extra chance each per cycle.
function randomInt(max, randomBytes) {
  const draw = randomBytes || secureRandomBytes;
  if (!Number.isInteger(max) || max < 1) {
    throw new Error(`randomInt needs a positive range, got ${max}`);
  }
  if (max === 1) return 0;
  let byteCount = 1;
  while (256 ** byteCount < max) byteCount += 1;
  const range = 256 ** byteCount;
  // The largest multiple of max the range holds; everything above it is the
  // biased tail and gets redrawn.
  const limit = range - (range % max);
  for (;;) {
    const bytes = draw(byteCount);
    let value = 0;
    for (let i = 0; i < byteCount; i += 1) value = value * 256 + bytes[i];
    if (value < limit) return value % max;
  }
}

/**
 * The classes a request enables, after exclusions, plus the alphabet they
 * form. Throws rather than quietly generating something weaker than asked.
 */
function alphabetFor(opts) {
  const excluded = opts.avoidLookalikes ? opts.lookalikes || LOOKALIKES : '';
  const classes = [];
  for (const id of CLASS_IDS) {
    if (!opts[id]) continue;
    const chars = [...CLASS_CHARS[id]].filter((ch) => !excluded.includes(ch)).join('');
    if (!chars) {
      throw new Error(`excluding those characters leaves no ${id} to draw from`);
    }
    classes.push(chars);
  }
  if (!classes.length) {
    throw new Error('pick at least one character class');
  }
  return { classes, all: classes.join('') };
}

function generatePassword(opts, deps) {
  const draw = (deps && deps.randomBytes) || secureRandomBytes;
  const { classes, all } = alphabetFor(opts);
  const length = opts.length;
  if (!Number.isInteger(length) || length < 1) {
    throw new Error(`a password needs a positive length, got ${length}`);
  }
  if (length < classes.length) {
    throw new Error(
      `${length} characters is too short to carry ${classes.length} character classes`
    );
  }
  // Draw the whole string, keep it only if every requested class turned up.
  // Placing one character per class and shuffling the rest would also satisfy
  // the requirement, but not uniformly: it fixes how many characters of each
  // class the first positions hold.
  for (let attempt = 0; attempt < MAX_DRAWS; attempt += 1) {
    let value = '';
    for (let i = 0; i < length; i += 1) value += all[randomInt(all.length, draw)];
    if (classes.every((chars) => [...value].some((ch) => chars.includes(ch)))) {
      return value;
    }
  }
  throw new Error('gave up drawing a password carrying every requested class');
}

function generatePassphrase(opts, deps) {
  const draw = (deps && deps.randomBytes) || secureRandomBytes;
  const words = (deps && deps.wordlist) || defaultWordlist();
  const count = opts.words;
  if (!Number.isInteger(count) || count < 1) {
    throw new Error(`a passphrase needs a positive word count, got ${count}`);
  }
  const separator = opts.separator === undefined ? '-' : opts.separator;
  const drawn = [];
  for (let i = 0; i < count; i += 1) {
    const word = words[randomInt(words.length, draw)];
    drawn.push(opts.capitalise ? word[0].toUpperCase() + word.slice(1) : word);
  }
  return drawn.join(separator);
}

function generate(opts, deps) {
  return opts.mode === 'passphrase'
    ? generatePassphrase(opts, deps)
    : generatePassword(opts, deps);
}

// log2 of a BigInt, at full double precision: take the top 53 bits and add
// back the shift. Number(n) would overflow to Infinity past 2^1024, which a
// 64-character alphabet reaches well before the length slider does.
function log2BigInt(value) {
  const bits = value.toString(2).length;
  const shift = BigInt(Math.max(0, bits - 53));
  return Math.log2(Number(value >> shift)) + Number(shift);
}

/**
 * How many bits of entropy the *request* buys - the log2 of how many outputs
 * it can produce, not of how many the alphabet could produce.
 *
 * Requiring every class to appear rules some strings out, so the honest count
 * is an inclusion-exclusion over the required classes rather than the usual
 * length x log2(alphabet), which overstates it.
 */
function entropyBits(opts, deps) {
  if (opts.mode === 'passphrase') {
    const words = (deps && deps.wordlist) || defaultWordlist();
    // The separator is fixed and capitalising is deterministic: neither adds
    // an output the attacker has to try.
    return (opts.words || 0) * Math.log2(words.length);
  }
  const { classes, all } = alphabetFor(opts);
  const length = BigInt(opts.length);
  const total = BigInt(all.length);
  let count = 0n;
  for (let subset = 0; subset < 1 << classes.length; subset += 1) {
    let removed = 0n;
    let bits = 0;
    for (let i = 0; i < classes.length; i += 1) {
      if (subset & (1 << i)) {
        removed += BigInt(classes[i].length);
        bits += 1;
      }
    }
    const strings = (total - removed) ** length;
    count += bits % 2 === 0 ? strings : -strings;
  }
  return count > 0n ? log2BigInt(count) : 0;
}

window.passwordGenerator = {
  UPPER,
  LOWER,
  DIGITS,
  SYMBOLS,
  LOOKALIKES,
  secureRandomBytes,
  defaultWordlist,
  randomInt,
  alphabetFor,
  generatePassword,
  generatePassphrase,
  generate,
  entropyBits,
};

// ---------------------------------------------------------------- the panel

// What a device remembers between two openings. The generated value is
// deliberately not in this list: options are a preference, a password is a
// secret, and localStorage is the wrong place for the second.
const OPTION_KEYS = [
  'mode',
  'length',
  'upper',
  'lower',
  'digits',
  'symbols',
  'avoidLookalikes',
  'words',
  'separator',
  'capitalise',
];
const OPTIONS_STORAGE_KEY = 'passwordGenerator.options';
// A host takes the value back by dispatching this on window - a vault does it
// when it locks. Named for what it asks rather than for who asks it, so this
// file stays free of any module's vocabulary.
const CLEAR_EVENT = 'password-generator-clear';

window.passwordGeneratorPanel = function passwordGeneratorPanel(deps) {
  return {
    open: false,
    mode: 'password',
    length: 20,
    upper: true,
    lower: true,
    digits: true,
    symbols: true,
    avoidLookalikes: false,
    words: 6,
    separator: '-',
    capitalise: true,
    value: '',
    error: '',

    init() {
      this.restore();
      for (const key of OPTION_KEYS) this.$watch(key, () => this.optionsChanged());
      // Bound once and kept, so destroy() can hand back the same reference.
      this.onClearRequest = () => this.clear();
      window.addEventListener(CLEAR_EVENT, this.onClearRequest);
      this.regenerate();
    },

    destroy() {
      window.removeEventListener(CLEAR_EVENT, this.onClearRequest);
      this.clear();
    },

    options() {
      const picked = {};
      for (const key of OPTION_KEYS) picked[key] = this[key];
      return picked;
    },

    restore() {
      let stored = null;
      try {
        stored = JSON.parse(window.localStorage.getItem(OPTIONS_STORAGE_KEY) || 'null');
      } catch (error) {
        stored = null;
      }
      if (!stored) return;
      for (const key of OPTION_KEYS) {
        if (stored[key] !== undefined) this[key] = stored[key];
      }
    },

    optionsChanged() {
      try {
        window.localStorage.setItem(OPTIONS_STORAGE_KEY, JSON.stringify(this.options()));
      } catch (error) {
        // A browser refusing storage is not a reason to refuse a password.
      }
      this.regenerate();
    },

    regenerate() {
      try {
        this.value = generate(this.options(), deps);
        this.error = '';
      } catch (failure) {
        // The old value answered the old options; showing it under the new
        // ones would read as the generator's answer to them.
        this.value = '';
        this.error = failure.message;
      }
    },

    clear() {
      this.value = '';
    },

    entropy() {
      try {
        return entropyBits(this.options(), deps);
      } catch (failure) {
        return 0;
      }
    },

    // Rough bands, in bits: below 60 a determined attacker gets there, above
    // 100 nothing does. The wording stays vague on purpose - a precise claim
    // would depend on the attacker's hardware, which this page cannot know.
    strength() {
      const bits = this.entropy();
      if (bits < 45) return { label: 'Weak', css: 'progress-error' };
      if (bits < 70) return { label: 'Good', css: 'progress-warning' };
      return { label: 'Strong', css: 'progress-success' };
    },

    // The value split into runs of one kind, so digits and symbols can be
    // picked out at a glance - reading a password back is how a typo is found.
    segments() {
      const runs = [];
      for (const char of this.value) {
        const kind = DIGITS.includes(char)
          ? 'text-info'
          : UPPER.includes(char) || LOWER.includes(char)
            ? ''
            : 'text-warning';
        const last = runs[runs.length - 1];
        if (last && last.css === kind) last.text += char;
        else runs.push({ text: char, css: kind });
      }
      return runs;
    },

    apply() {
      if (!this.value) return;
      this.$dispatch('password-apply', { value: this.value });
      this.open = false;
    },

    copy() {
      if (!this.value) return;
      this.$dispatch('password-copy', { value: this.value });
    },
  };
};
