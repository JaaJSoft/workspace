import { canonicalCbor, decodeCbor } from './cbor.js';
import { equalBytes } from './encoding.js';

// One byte in front of every persisted signature, so a future algorithm lands
// without a data migration. 0x02 is reserved for Ed25519 + ML-DSA-44.
export const SIG_ALG_ED25519 = 0x01;

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

// Not everything signed is a CBOR payload: the account key attestation signs a
// plain ASCII string built by the associated-data catalogue. Routing it through
// sign() would wrap it in a CBOR byte string and produce a signature no
// conforming verifier accepts.
export async function signBytes(privateRaw, message) {
  const key = await crypto.subtle.importKey(
    'pkcs8', toPkcs8(privateRaw), 'Ed25519', false, ['sign']
  );
  const signature = new Uint8Array(await crypto.subtle.sign('Ed25519', key, message));
  const out = new Uint8Array(1 + signature.length);
  out[0] = SIG_ALG_ED25519;
  out.set(signature, 1);
  return out;
}

export async function verifyBytes(publicRaw, message, signature) {
  if (signature[0] !== SIG_ALG_ED25519) {
    throw new Error(`unsupported signature algorithm ${signature[0]}`);
  }
  const key = await crypto.subtle.importKey('raw', publicRaw, 'Ed25519', false, ['verify']);
  const ok = await crypto.subtle.verify('Ed25519', key, signature.slice(1), message);
  if (!ok) throw new Error('signature does not verify');
}

export async function sign(privateRaw, payload) {
  return signBytes(privateRaw, canonicalCbor(payload));
}

// The session form: a non-extractable CryptoKey, so the seed can be zeroed at
// once and no later call needs the raw bytes back.
export async function importSigner(seed) {
  const key = await crypto.subtle.importKey(
    'pkcs8', toPkcs8(seed), 'Ed25519', false, ['sign']
  );
  return {
    async sign(message) {
      const signature = new Uint8Array(await crypto.subtle.sign('Ed25519', key, message));
      const out = new Uint8Array(1 + signature.length);
      out[0] = SIG_ALG_ED25519;
      out.set(signature, 1);
      return out;
    },
  };
}

export async function verify(publicRaw, payloadBytes, signature, expectedType) {
  const payload = decodeCbor(payloadBytes);              // 1. decode
  if (payload.v !== 1) throw new Error(`unsupported payload version ${payload.v}`);  // 2.
  if (payload.type !== expectedType) {                   // 3. type, before any crypto
    throw new Error(`payload type ${payload.type} does not match ${expectedType}`);
  }
  if (!equalBytes(canonicalCbor(payload), payloadBytes)) {  // 4. re-canonicalise
    throw new Error('payload is not canonically encoded');
  }
  await verifyBytes(publicRaw, payloadBytes, signature);                       // 5.
  return payload;
}
