import { randomBytes } from './encoding.js';
import { AEAD_AES_256_GCM, decodeCiphertext, encodeCiphertext } from './wire.js';

const KEY_LENGTH = 32;
const IV_LENGTH = 12;

// A CryptoKey is not a Uint8Array and has no realm-safe brand, so the test is
// "does it look like raw material": everything else is handed to WebCrypto as
// a key it already is. Both forms must produce identical bytes - the parity
// vectors are what say so.
const isRawKey = (key) => ArrayBuffer.isView(key) || key instanceof ArrayBuffer;

// Both usages by default: a vault metadata key seals and opens across one
// session. A caller needing only one passes it.
export function importAeadKey(raw, usages = ['encrypt', 'decrypt']) {
  if (raw.length !== KEY_LENGTH) {
    throw new Error(`aes-256-gcm needs a ${KEY_LENGTH}-byte key, got ${raw.length}`);
  }
  return crypto.subtle.importKey('raw', raw, 'AES-GCM', false, usages);
}

async function subtleKeyFor(key, usage) {
  if (!isRawKey(key)) return key;
  // WebCrypto picks the AES variant from the key length, so a 16-byte key
  // would quietly produce AES-128-GCM under a header still declaring
  // AES-256-GCM - the agility byte would be a lie.
  if (key.length !== KEY_LENGTH) {
    throw new Error(`aes-256-gcm needs a ${KEY_LENGTH}-byte key, got ${key.length}`);
  }
  return crypto.subtle.importKey('raw', key, 'AES-GCM', false, [usage]);
}

// The iv is drawn by default; pinning it is for the parity vectors, where
// determinism is the point. 96 random bits are safe here only because the key
// is per entry: the seals under one key stay far below the birthday bound.
export async function seal(
  key, plaintext, associatedData, { iv = randomBytes(IV_LENGTH), keyVersion, kdfId }
) {
  const subtleKey = await subtleKeyFor(key, 'encrypt');
  const ciphertext = new Uint8Array(
    await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv, additionalData: associatedData, tagLength: 128 },
      subtleKey,
      plaintext
    )
  );
  return encodeCiphertext({ aeadId: AEAD_AES_256_GCM, kdfId, keyVersion, iv, ciphertext });
}

export async function open(key, raw, associatedData) {
  const decoded = decodeCiphertext(raw);
  const subtleKey = await subtleKeyFor(key, 'decrypt');
  // An open failure propagates as-is: never retried with another AD, never
  // returned as partial plaintext. Retrying turns the AD into an oracle.
  const plain = await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: decoded.iv, additionalData: associatedData, tagLength: 128 },
    subtleKey,
    decoded.ciphertext
  );
  return new Uint8Array(plain);
}
