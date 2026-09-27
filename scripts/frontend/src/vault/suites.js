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
// registerAead; the others are fixed by the code that implements them.
const IMPLEMENTED = {
  format: new Set([1, 2]),
  aead: new Set(),
  pubkey: new Set([1, 2]),
  signature: new Set([1]),
  payload: new Set([1]),
};

export function markImplemented(axis, id) {
  IMPLEMENTED[axis].add(id);
}

export function implementedIds(axis) {
  return Array.from(IMPLEMENTED[axis]).sort((a, b) => a - b);
}

// A 'test' entry is readable once something registered it - which only the
// test-suite script does. Anything not implemented is unsupported, whatever
// the manifest says: declared is not the same as readable.
export function suiteEntry(axis, id) {
  const entry = MANIFEST[axis][String(id)];
  if (!entry || !IMPLEMENTED[axis] || !IMPLEMENTED[axis].has(Number(id))) {
    throw new UnsupportedAlgorithmError(axis, id);
  }
  return entry;
}

export function declaredEntry(axis, id) {
  return MANIFEST[axis][String(id)];
}

export function hpkeEntry(format) {
  const entry = MANIFEST.hpke[String(format)];
  if (!entry || entry.state === 'test') throw new UnsupportedAlgorithmError('hpke', format);
  return entry;
}

export function kdfEntry(algo) {
  const entry = Object.prototype.hasOwnProperty.call(MANIFEST.kdf, algo) ? MANIFEST.kdf[algo] : null;
  if (!entry || entry.state === 'test') throw new UnsupportedAlgorithmError('kdf', algo);
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
