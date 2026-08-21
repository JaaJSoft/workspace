import { randomBytes } from './encoding.js';
import { AEAD_AES_256_GCM, decodeCiphertext, encodeCiphertext } from './wire.js';

const KEY_LENGTH = 32;
const IV_LENGTH = 12;

// The iv is drawn by default; pinning it is for the parity vectors, where
// determinism is the point. 96 random bits are safe here only because the key
// is per entry: the seals under one key stay far below the birthday bound.
export async function seal(
  key, plaintext, associatedData, { iv = randomBytes(IV_LENGTH), keyVersion, kdfId }
) {
  // WebCrypto picks the AES variant from the key length, so a 16-byte key
  // would quietly produce AES-128-GCM under a header still declaring
  // AES-256-GCM - the agility byte would be a lie.
  if (key.length !== KEY_LENGTH) {
    throw new Error(`aes-256-gcm needs a ${KEY_LENGTH}-byte key, got ${key.length}`);
  }
  const subtleKey = await crypto.subtle.importKey('raw', key, 'AES-GCM', false, ['encrypt']);
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
  if (key.length !== KEY_LENGTH) {
    throw new Error(`aes-256-gcm needs a ${KEY_LENGTH}-byte key, got ${key.length}`);
  }
  const decoded = decodeCiphertext(raw);
  const subtleKey = await crypto.subtle.importKey('raw', key, 'AES-GCM', false, ['decrypt']);
  // An open failure propagates as-is: never retried with another AD, never
  // returned as partial plaintext. Retrying turns the AD into an oracle.
  const plain = await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: decoded.iv, additionalData: associatedData, tagLength: 128 },
    subtleKey,
    decoded.ciphertext
  );
  return new Uint8Array(plain);
}
