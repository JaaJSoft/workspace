import { Encoder, decode } from 'cbor-x';

// cbor-x does NOT produce deterministic CBOR with its defaults, and every one
// of these options is load-bearing: records and object-maps each change the
// encoding, and largeBigIntToFloat would smuggle in a float.
const encoder = new Encoder({
  useRecords: false,
  mapsAsObjects: false,
  largeBigIntToFloat: false,
  variableMapSize: true,
  // Byte strings would otherwise be wrapped in tag 64, and canonical CBOR
  // admits no tags. The reference implementation emits them bare.
  tagUint8Array: false,
});

// Realm-agnostic type test. `instanceof` and prototype identity both fail
// across realms, and this module is loaded into a vm context by the test
// suite - the same trap that made cbor-x emit indefinite-length arrays there.
const kindOf = (value) => Object.prototype.toString.call(value);

const UINT32_MAX = 0xffffffff;
const INT32_MIN = -0x80000000;
const UNENCODABLE_NEGATIVE_LOW = -0x100000000;
const UNENCODABLE_NEGATIVE_HIGH = -0x80000001;
const keyEncoder = new TextEncoder();

// CBOR sorts map keys by their ENCODED bytes (RFC 8949 §4.2.1): shorter first,
// then bytewise. Text keys of equal encoded length share a head byte, so
// comparing their UTF-8 bytes reproduces that order exactly. Sorting on
// String.length instead would agree only for ASCII keys - 'é' is one UTF-16
// unit but two UTF-8 bytes, and would sort ahead of 'zz' where the reference
// implementation puts it after.
function compareKeys(a, b) {
  const left = keyEncoder.encode(a);
  const right = keyEncoder.encode(b);
  if (left.length !== right.length) return left.length - right.length;
  for (let i = 0; i < left.length; i += 1) {
    if (left[i] !== right[i]) return left[i] - right[i];
  }
  return 0;
}

function normaliseMap(value) {
  const entries = kindOf(value) === '[object Map]' ? [...value] : Object.entries(value);
  for (const [key] of entries) {
    if (typeof key !== 'string') {
      // The reference keeps a non-string key as itself; String(key) here would
      // encode an integer key as text and sign different bytes.
      throw new Error(`canonical CBOR map keys must be strings, got ${kindOf(key)}`);
    }
  }
  // Keys are normalised before sorting: normalising afterwards could reorder
  // the map behind the comparator's back.
  const folded = entries.map(([key, item]) => [key.normalize('NFC'), item]);
  // Two keys that are one key once normalised: each implementation would keep
  // a different one of the two values, so neither keeps either.
  const seen = new Set();
  for (const [key] of folded) {
    if (seen.has(key)) throw new Error(`map keys collide after NFC normalisation: ${key}`);
    seen.add(key);
  }
  folded.sort(([a], [b]) => compareKeys(a, b));
  return new Map(folded.map(([key, item]) => [key, normalise(item)]));
}

function normalise(value) {
  if (typeof value === 'bigint') return value;
  if (typeof value === 'boolean' || value === null) return value;
  if (typeof value === 'string') return value.normalize('NFC');
  if (typeof value === 'number') {
    if (!Number.isInteger(value)) throw new Error('floats are forbidden in canonical CBOR');
    // Negative integers in this band encode canonically as a four-byte
    // argument, which cbor-x cannot produce: its BigInt path always writes
    // eight. Refused rather than signed differently on each side.
    if (value >= UNENCODABLE_NEGATIVE_LOW && value <= UNENCODABLE_NEGATIVE_HIGH) {
      throw new Error(`integer ${value} has no encoding both implementations agree on`);
    }
    // cbor-x encodes any integer outside the 32-bit range as a float64, the
    // one thing canonical CBOR forbids. A BigInt forces the integer encoding
    // the reference implementation writes - millisecond timestamps are
    // precisely the values that fall in that hole.
    return value > UINT32_MAX || value < INT32_MIN ? BigInt(value) : value;
  }
  // Rebuilt with this realm's constructors, not the caller's: cbor-x branches
  // on `constructor === Array`, and a foreign array would fall through to its
  // iterator path and emit an indefinite length.
  switch (kindOf(value)) {
    case '[object Array]':
      return Array.from(value, normalise);
    case '[object Uint8Array]':
      return Uint8Array.from(value);
    case '[object Map]':
    case '[object Object]':
      return normaliseMap(value);
    default:
      // Everything else - Date, Set, undefined, a class instance - has no
      // agreed encoding. Guessing one means signing bytes the reference would
      // never produce.
      throw new Error(`unsupported type in canonical CBOR: ${kindOf(value)}`);
  }
}

export function canonicalCbor(payload) {
  // cbor-x hands back a view into a buffer it keeps and reuses between calls
  // (`return target.subarray(start, position)`), not a buffer of its own. The
  // copy is what the caller gets to keep and to wipe; the view is wiped here,
  // because otherwise the largest plaintext this module ever produces - the
  // whole account, on an export - stays resident in that buffer after the
  // vault locks, and no caller can reach it to do anything about it.
  const view = encoder.encode(normalise(payload));
  const encoded = new Uint8Array(view);
  view.fill(0);
  // Re-decode check: structurally invalid output is caught here rather than by
  // a signature that stops verifying. It does not catch a tag or an indefinite
  // length - cbor-x decodes both quite happily - which is why the encoder is
  // configured to emit neither.
  decode(encoded);
  return encoded;
}

// The archive is sealed, not signed: nothing ever compares its bytes, so it
// takes no part of the canonical form. Normalising would change what it
// stores - a password saved in NFD would come back in NFC, a different
// credential - and two custom field ids that fold to one under NFC would
// refuse the export outright. Keys keep their insertion order.
function plainValue(value) {
  if (typeof value === 'string' || typeof value === 'boolean' || value === null) return value;
  if (typeof value === 'bigint') return value;
  if (typeof value === 'number') {
    // Same hole as the canonical path: outside 32 bits cbor-x writes a float.
    return Number.isInteger(value) && (value > UINT32_MAX || value < INT32_MIN)
      ? BigInt(value)
      : value;
  }
  // Rebuilt with this realm's constructors, for the reason normalise() gives.
  switch (kindOf(value)) {
    case '[object Array]':
      return Array.from(value, plainValue);
    case '[object Uint8Array]':
      return Uint8Array.from(value);
    case '[object Map]':
    case '[object Object]': {
      const entries = kindOf(value) === '[object Map]' ? [...value] : Object.entries(value);
      return new Map(entries.map(([key, item]) => [key, plainValue(item)]));
    }
    default:
      throw new Error(`unsupported type in an archive payload: ${kindOf(value)}`);
  }
}

// cbor-x reserves room before it writes rather than after, and a string
// reserves three bytes per UTF-16 unit whatever it ends up encoding to. The
// bound follows those reservations, not the final size: an arena sized to the
// final size would still be outgrown halfway through a long string.
const ITEM_HEAD = 9;
// encode() refuses to start within 0x800 bytes of the end of its buffer and
// stops 10 bytes short of it.
const ARENA_SLACK = 0x800 + 64;
// What cbor-x gets back once an arena is done with: small enough to keep.
const RELEASED_BUFFER_SIZE = 8192;

function sizeBound(value) {
  if (typeof value === 'string') return ITEM_HEAD + value.length * 3;
  if (typeof value !== 'object' || value === null) return ITEM_HEAD;
  switch (kindOf(value)) {
    case '[object Array]':
      return value.reduce((sum, item) => sum + sizeBound(item), ITEM_HEAD);
    case '[object Uint8Array]':
      return ITEM_HEAD + value.length;
    default: {
      const entries = kindOf(value) === '[object Map]' ? [...value] : Object.entries(value);
      return entries.reduce(
        (sum, [key, item]) => sum + sizeBound(key) + sizeBound(item), ITEM_HEAD
      );
    }
  }
}

export function cborSizeBound(payload) {
  return sizeBound(payload) + ARENA_SLACK;
}

// Encodes into an arena the caller allocated with cborSizeBound and wipes
// itself. Left to its own buffer, cbor-x grows it by allocating a larger one
// and copying across, and every buffer it grows past keeps a prefix of the
// plaintext - header, first vault, first passwords - that nothing can reach
// to wipe.
export function encodeCbor(payload, arena) {
  const value = plainValue(payload);
  encoder.useBuffer(arena);
  let view;
  try {
    view = encoder.encode(value);
  } finally {
    // cbor-x's buffer is shared by every encoder in the realm. Left on the
    // arena, the next signature payload would be written into the caller's
    // memory, and the arena would never be released.
    encoder.useBuffer(new Uint8Array(RELEASED_BUFFER_SIZE));
  }
  if (view.buffer !== arena.buffer) {
    // Grown after all, so a copy the arena does not hold already exists. The
    // bound is there so this cannot happen; if it does, nothing is sealed.
    view.fill(0);
    throw new Error('the payload outgrew the arena sized for it');
  }
  return view;
}

export function decodeCbor(bytes) {
  const decoded = decode(bytes);
  return decoded instanceof Map ? Object.fromEntries(decoded) : decoded;
}
