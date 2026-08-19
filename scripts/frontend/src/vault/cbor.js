import { Encoder, decode } from 'cbor-x';

// cbor-x does NOT produce deterministic CBOR with its defaults, and every one
// of these options is load-bearing: records and object-maps each change the
// encoding, and largeBigIntToFloat would smuggle in a float.
const encoder = new Encoder({
  useRecords: false,
  mapsAsObjects: false,
  largeBigIntToFloat: false,
  variableMapSize: true,
});

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

function normalise(value) {
  if (Array.isArray(value)) return value.map(normalise);
  if (typeof value === 'bigint') return value;
  // Two spellings of the same accented text are different byte strings and
  // would sign as different values of what a user sees as one. Normalising is
  // part of the encoding, not the caller's problem.
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
  if (!value || typeof value !== 'object') return value;
  // Keys are normalised before sorting: normalising afterwards could reorder
  // the map behind the comparator's back.
  const entries = (value instanceof Map ? [...value] : Object.entries(value)).map(
    ([key, item]) => [String(key).normalize('NFC'), item]
  );
  // Two keys that are one key once normalised: each implementation would keep
  // a different one of the two values, so neither keeps either.
  const seen = new Set();
  for (const [key] of entries) {
    if (seen.has(key)) throw new Error(`map keys collide after NFC normalisation: ${key}`);
    seen.add(key);
  }
  entries.sort(([a], [b]) => compareKeys(a, b));
  return new Map(entries.map(([key, item]) => [key, normalise(item)]));
}

export function canonicalCbor(payload) {
  const encoded = new Uint8Array(encoder.encode(normalise(payload)));
  // Re-decode check: an encoder that silently emitted a tag or an indefinite
  // length is caught here rather than by a signature that stops verifying.
  decode(encoded);
  return encoded;
}

export function decodeCbor(bytes) {
  const decoded = decode(bytes);
  return decoded instanceof Map ? Object.fromEntries(decoded) : decoded;
}
