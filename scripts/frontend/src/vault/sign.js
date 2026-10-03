import { canonicalCbor, decodeCbor } from './cbor.js';
import { equalBytes } from './encoding.js';
import { currentSuite, markImplemented, suiteEntry } from './suites.js';

// One byte in front of every persisted signature, so a future algorithm lands
// without a data migration.
function prefixed(signature) {
  const out = new Uint8Array(1 + signature.length);
  out[0] = currentSuite().signatureAlg;
  out.set(signature, 1);
  return out;
}

// WebCrypto imports an Ed25519 PUBLIC key as 'raw' but refuses a private one -
// it only accepts 'pkcs8' or 'jwk'. The vectors carry the bare 32-byte seed,
// so it gets the fixed PKCS#8 prelude here rather than at every call site.
const PKCS8_ED25519_PREFIX = Uint8Array.from([
  0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20,
]);

function toPkcs8(seed) {
  if (seed.length !== 32) throw new Error(`Ed25519 seed is ${seed.length} bytes, expected 32`);
  const out = new Uint8Array(PKCS8_ED25519_PREFIX.length + 32);
  out.set(PKCS8_ED25519_PREFIX, 0);
  out.set(seed, PKCS8_ED25519_PREFIX.length);
  return out;
}

const ed25519 = {
  async importSigningKey(seed) {
    return crypto.subtle.importKey('pkcs8', toPkcs8(seed), 'Ed25519', false, ['sign']);
  },
  async sign(key, message) {
    return new Uint8Array(await crypto.subtle.sign('Ed25519', key, message));
  },
  async verify(publicRaw, signature, message) {
    const key = await crypto.subtle.importKey('raw', publicRaw, 'Ed25519', false, ['verify']);
    return crypto.subtle.verify('Ed25519', key, signature, message);
  },
};

// Keyed by the signature's one-byte prefix, as the server's verifiers are. The
// prefix picks the algorithm on both sides: a second id declared in the
// manifest stays unsupported until it lands here, instead of being checked as
// Ed25519.
const SIGNATURES = new Map([[1, ed25519]]);
for (const id of SIGNATURES.keys()) markImplemented('signature', id);

function currentSignature() {
  const alg = currentSuite().signatureAlg;
  suiteEntry('signature', alg);
  return SIGNATURES.get(alg);
}

// Not everything signed is a CBOR payload: the account key attestation signs a
// plain ASCII string built by the associated-data catalogue. Routing it through
// sign() would wrap it in a CBOR byte string and produce a signature no
// conforming verifier accepts.
export async function signBytes(privateRaw, message) {
  const alg = currentSignature();
  return prefixed(await alg.sign(await alg.importSigningKey(privateRaw), message));
}

export async function verifyBytes(publicRaw, message, signature) {
  if (signature.length < 1) throw new Error('signature is empty');
  const entry = suiteEntry('signature', signature[0]);
  if (signature.length !== 1 + entry.length) throw new Error('signature has the wrong length');
  const ok = await SIGNATURES.get(signature[0]).verify(publicRaw, signature.slice(1), message);
  if (!ok) throw new Error('signature does not verify');
}

export async function sign(privateRaw, payload) {
  return signBytes(privateRaw, canonicalCbor(payload));
}

// The session form: a non-extractable CryptoKey, so the seed can be zeroed at
// once and no later call needs the raw bytes back.
export async function importSigner(seed) {
  const alg = currentSignature();
  const key = await alg.importSigningKey(seed);
  return {
    async sign(message) {
      return prefixed(await alg.sign(key, message));
    },
  };
}

export async function verify(publicRaw, payloadBytes, signature, expectedType) {
  const payload = decodeCbor(payloadBytes);              // 1. decode
  suiteEntry('payload', payload.v);                      // 2. version
  if (payload.type !== expectedType) {                   // 3. type, before any crypto
    throw new Error(`payload type ${payload.type} does not match ${expectedType}`);
  }
  if (!equalBytes(canonicalCbor(payload), payloadBytes)) {  // 4. re-canonicalise
    throw new Error('payload is not canonically encoded');
  }
  await verifyBytes(publicRaw, payloadBytes, signature);                       // 5.
  return payload;
}
