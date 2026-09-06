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

// A closed set rather than a free character. An empty one would run the words
// together, and two draws can then spell the same string while the reported
// entropy counts draws - the one place this file would overstate.
const SEPARATORS = [
  { value: '-', label: 'Hyphen' },
  { value: '.', label: 'Period' },
  { value: '_', label: 'Underscore' },
  { value: ',', label: 'Comma' },
  { value: ' ', label: 'Space' },
];

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

// Uniform in [0, max): keep the low bits, redraw anything past the range.
//
// Never a modulo. `byte % 62` hands the first eight characters an extra chance
// each per 256-byte cycle, and even the unbiased form - a modulo of a value
// already narrowed to a multiple of max - reads as that bug to anything
// scanning for it. Masking says the same thing without the ambiguity, and
// never rejects more than half the draws.
function randomInt(max, randomBytes) {
  const draw = randomBytes || secureRandomBytes;
  if (!Number.isInteger(max) || max < 1) {
    throw new Error(`randomInt needs a positive range, got ${max}`);
  }
  if (max === 1) return 0;
  let mask = 1;
  while (mask < max - 1) mask = mask * 2 + 1;
  const byteCount = Math.ceil(mask.toString(2).length / 8);
  for (;;) {
    const bytes = draw(byteCount);
    let value = 0;
    for (let i = 0; i < byteCount; i += 1) value = value * 256 + bytes[i];
    value &= mask;
    if (value < max) return value;
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
  // Defended here too, not only in the panel: this is exported, and a caller
  // passing '' would get words with no boundary under an entropy figure that
  // assumes there is one.
  const separator = opts.separator ? opts.separator : '-';
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
    // an output the attacker has to try. It does have to be *there*: with the
    // words run together the boundaries are lost, two draws can spell the
    // same string and this count becomes an upper bound.
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
  SEPARATORS,
};

// ---------------------------------------------------------------- the panel

// What a device remembers between two openings, and what each key is allowed
// to be. The generated value is deliberately absent: options are a preference,
// a password is a secret, and localStorage is the wrong place for the second.
//
// Every key is checked on the way back in rather than trusted. Storage is
// user-writable and outlives any version of this file, and a value that got
// through would not fail loudly: a mode of 'foo' hides both option panes with
// neither tab active, a length of 999 draws 999 characters under a slider
// pinned at 64. The bounds are the ones the partial's controls offer.
const OPTION_RULES = {
  mode: { oneOf: ['password', 'passphrase'] },
  length: { min: 8, max: 64 },
  upper: { boolean: true },
  lower: { boolean: true },
  digits: { boolean: true },
  symbols: { boolean: true },
  avoidLookalikes: { boolean: true },
  words: { min: 3, max: 12 },
  separator: { oneOf: SEPARATORS.map((choice) => choice.value) },
  capitalise: { boolean: true },
};
const OPTION_KEYS = Object.keys(OPTION_RULES);
const OPTIONS_STORAGE_KEY = 'passwordGenerator.options';

function isStorableOption(key, value) {
  const rule = OPTION_RULES[key];
  if (!rule) return false;
  if (rule.boolean) return typeof value === 'boolean';
  if (rule.oneOf) return rule.oneOf.includes(value);
  return Number.isInteger(value) && value >= rule.min && value <= rule.max;
}

/**
 * `pinned` is the options a host fixes for its own form. They outrank what the
 * device remembered - a host pins a key because its dialog needs that value,
 * not as a suggestion - and they are kept out of what persist() writes, so one
 * host's choice never becomes every other host's default: the storage key is
 * shared by all of them.
 */
window.passwordGeneratorPanel = function passwordGeneratorPanel(deps, pinned) {
  const fixed = pinned || {};
  const fixedKeys = OPTION_KEYS.filter((key) => isStorableOption(key, fixed[key]));
  return {
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
    bits: 0,
    error: '',

    separators: SEPARATORS,

    init() {
      this.restore();
      for (const key of fixedKeys) this[key] = fixed[key];
      for (const key of OPTION_KEYS) this.$watch(key, () => this.regenerate());
      // The mode is a pair of buttons, so no change event ever bubbles for it
      // and the root's @change cannot persist it.
      this.$watch('mode', () => this.persist());
      this.regenerate();
    },

    destroy() {
      this.clear();
    },

    options() {
      const picked = {};
      for (const key of OPTION_KEYS) picked[key] = this[key];
      return picked;
    },

    // Unreadable storage is the same as empty storage: a browser refusing it,
    // or a value someone hand-edited into nonsense, must not stop the panel.
    storedOptions() {
      try {
        return JSON.parse(window.localStorage.getItem(OPTIONS_STORAGE_KEY) || 'null') || {};
      } catch (error) {
        return {};
      }
    },

    restore() {
      const stored = this.storedOptions();
      for (const key of OPTION_KEYS) {
        if (isStorableOption(key, stored[key])) this[key] = stored[key];
      }
    },

    persist() {
      const kept = this.options();
      // A pinned key is written back as whatever the device already
      // remembered, never as the pinned value and never dropped: the stored
      // options are shared by every host, so one form's requirement must not
      // become every form's default, nor erase what another form had stored.
      const stored = fixedKeys.length ? this.storedOptions() : {};
      for (const key of fixedKeys) {
        if (isStorableOption(key, stored[key])) kept[key] = stored[key];
        else delete kept[key];
      }
      try {
        window.localStorage.setItem(OPTIONS_STORAGE_KEY, JSON.stringify(kept));
      } catch (error) {
        // A browser refusing storage is not a reason to refuse a password.
      }
    },

    regenerate() {
      try {
        this.value = generate(this.options(), deps);
        // Computed once per draw rather than read from the bindings: the
        // inclusion-exclusion below is BigInt work with exponents up to 64,
        // and the strength row reads it three times per reactive flush.
        this.bits = entropyBits(this.options(), deps);
        this.error = '';
      } catch (failure) {
        // The old value answered the old options; showing it under the new
        // ones would read as the generator's answer to them.
        this.value = '';
        this.bits = 0;
        this.error = failure.message;
      }
    },

    clear() {
      this.value = '';
    },

    // Enter inside the host's form. Chromium submits implicitly from a range
    // and from a checkbox - only the select is exempt - so pressing it here
    // would save the entry with the password the draft already held, and tear
    // this panel down with the drawn one still in it. Buttons are left alone:
    // Enter is how they are pressed from the keyboard.
    blockImplicitSubmit(event) {
      const target = event.target;
      if (target && target.closest && target.closest('button, a')) return;
      event.preventDefault();
    },

    // Three rough bands, in bits: under 45 an offline attacker gets there,
    // 45 to 70 is a real but not comfortable margin, past 70 nothing plausible
    // reaches it. The wording stays vague on purpose - a precise claim would
    // depend on the attacker's hardware, which this page cannot know.
    strength() {
      if (this.bits < 45) return { label: 'Weak', css: 'progress-error' };
      if (this.bits < 70) return { label: 'Good', css: 'progress-warning' };
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
    },

    copy() {
      if (!this.value) return;
      this.$dispatch('password-copy', { value: this.value });
    },
  };
};
