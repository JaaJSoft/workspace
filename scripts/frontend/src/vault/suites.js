// The crypto suite manifest, embedded at build time. The server never sends
// it: a server able to widen what this bundle accepts would choose the
// algorithm for the client.
import MANIFEST from '../../../../workspace/vault/crypto_suites.json';

export class UnsupportedAlgorithmError extends Error {
  constructor(axis, identifier) {
    super(`unsupported ${axis} ${identifier}`);
    this.name = 'UnsupportedAlgorithmError';
    this.axis = axis;
    this.identifier = identifier;
  }
}

// What this build can read, per axis. The AEAD axis is filled by aead.js and by
// registerAead, the KDF axis by kdf.js; the others are fixed by the code that
// implements them. The HPKE axis is keyed by format, as the manifest is, and
// hpke.js refuses any format whose declared suite is not the one construction
// it builds.
const IMPLEMENTED = {
  format: new Set([1, 2]),
  aead: new Set(),
  hpke: new Set([1, 2]),
  pubkey: new Set([1, 2]),
  signature: new Set([1]),
  payload: new Set([1]),
  kdf: new Set(),
};

export function markImplemented(axis, id) {
  IMPLEMENTED[axis].add(id);
}

// Numeric axes sort as numbers; the KDF axis is keyed by name.
export function implementedIds(axis) {
  return Array.from(IMPLEMENTED[axis]).sort((a, b) => (
    typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b))
  ));
}

// A 'test' entry is readable once something registered it - which only the
// test-suite script does. Anything not implemented is unsupported, whatever
// the manifest says: declared is not the same as readable.
// Object.hasOwn, not a bracket lookup: MANIFEST[axis] is a plain object, and
// 'constructor' or another Object.prototype name would otherwise resolve
// through the prototype chain to a value that is truthy but names no suite.
function ownEntry(axis, id) {
  const key = String(id);
  return Object.hasOwn(MANIFEST[axis], key) ? MANIFEST[axis][key] : null;
}

// A stored id arriving as the string "1" or as true is not the id 1: the
// manifest is keyed by strings, so only an integer check keeps them apart.
export function suiteEntry(axis, id) {
  if (!Number.isInteger(id)) throw new UnsupportedAlgorithmError(axis, id);
  const entry = ownEntry(axis, id);
  if (!entry || !IMPLEMENTED[axis] || !IMPLEMENTED[axis].has(Number(id))) {
    throw new UnsupportedAlgorithmError(axis, id);
  }
  return entry;
}

export function declaredEntry(axis, id) {
  return ownEntry(axis, id);
}

export function hpkeEntry(format) {
  const entry = Number.isInteger(format) ? ownEntry('hpke', format) : null;
  if (!entry || entry.state === 'test' || !IMPLEMENTED.hpke.has(format)) {
    throw new UnsupportedAlgorithmError('hpke', format);
  }
  return entry;
}

// One rule for every reader of a stored hpke_suite, the info builder included.
// Format 1 is spelled by the key's absence: an explicit 1 would pair a format-1
// suite with an info naming its suite, which no writer ever sealed under. The
// ids are compared by identity, so true or "32" never stand in for 1 or 32.
export function storedHpkeFormat(stored) {
  if (!stored || typeof stored !== 'object' || Array.isArray(stored)) {
    throw new UnsupportedAlgorithmError('hpke', stored);
  }
  const explicit = Object.hasOwn(stored, 'format');
  const format = explicit ? stored.format : 1;
  if (explicit && (!Number.isInteger(format) || format === 1)) {
    throw new UnsupportedAlgorithmError('hpke', format);
  }
  const declared = hpkeEntry(format).suite;
  const ids = Object.keys(stored).filter((key) => key !== 'format');
  if (ids.length !== Object.keys(declared).length
    || ids.some((key) => !Object.hasOwn(declared, key) || stored[key] !== declared[key])) {
    throw new UnsupportedAlgorithmError('hpke', format);
  }
  return format;
}

export function kdfEntry(algo) {
  const entry = typeof algo === 'string' ? ownEntry('kdf', algo) : null;
  if (!entry || entry.state === 'test' || !IMPLEMENTED.kdf.has(algo)) {
    throw new UnsupportedAlgorithmError('kdf', algo);
  }
  return entry;
}

function currentKey(axis, match = {}) {
  const keys = Object.entries(MANIFEST[axis])
    .filter(([, entry]) => entry.state === 'current'
      && Object.entries(match).every(([field, wanted]) => entry[field] === wanted))
    .map(([key]) => key);
  if (keys.length !== 1) throw new Error(`manifest axis ${axis} has ${keys.length} current entries`);
  return keys[0];
}

const hpkeFormat = currentKey('hpke');
const kdfAlgo = currentKey('kdf');

export const CURRENT_SUITE = Object.freeze({
  formatVersion: Number(currentKey('format')),
  aeadId: Number(currentKey('aead')),
  hpke: Object.freeze({ ...MANIFEST.hpke[hpkeFormat].suite, format: Number(hpkeFormat) }),
  kexPublicKeyAlg: Number(currentKey('pubkey', { usage: 'kex' })),
  sigPublicKeyAlg: Number(currentKey('pubkey', { usage: 'sig' })),
  signatureAlg: Number(currentKey('signature')),
  payloadVersion: Number(currentKey('payload')),
  kdf: Object.freeze({ algo: kdfAlgo, params: Object.freeze({ ...MANIFEST.kdf[kdfAlgo].params }) }),
});
