import { AEAD_AES_256_GCM, decodeCiphertext, encodeCiphertext } from './wire.js';

export async function seal(key, plaintext, associatedData, { iv, keyVersion, kdfId }) {
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
