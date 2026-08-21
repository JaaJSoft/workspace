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
  const encoded = new Uint8Array(encoder.encode(normalise(payload)));
  // Re-decode check: structurally invalid output is caught here rather than by
  // a signature that stops verifying. It does not catch a tag or an indefinite
  // length - cbor-x decodes both quite happily - which is why the encoder is
  // configured to emit neither.
  decode(encoded);
  return encoded;
}

export function decodeCbor(bytes) {
  const decoded = decode(bytes);
  return decoded instanceof Map ? Object.fromEntries(decoded) : decoded;
}
