import { randomBytes } from './encoding.js';
import { AEAD_AES_256_GCM, decodeCiphertext, encodeCiphertext } from './wire.js';

const KEY_LENGTH = 32;
const IV_LENGTH = 12;

// A CryptoKey is not a Uint8Array and has no realm-safe brand, so the test is
// "does it look like raw material": everything else is handed to WebCrypto as
// a key it already is. Both forms must produce identical bytes - the parity
// vectors are what say so.
const isRawKey = (key) => ArrayBuffer.isView(key) || key instanceof ArrayBuffer;

// byteLength, not length: an ArrayBuffer and a DataView are raw material this
// module accepts, and neither has a `length` at all - measuring by it rejects
// a perfectly good 32-byte key with "got undefined".
function assertRawKeyLength(key) {
  if (key.byteLength !== KEY_LENGTH) {
    throw new Error(`aes-256-gcm needs a ${KEY_LENGTH}-byte key, got ${key.byteLength}`);
  }
}

// Both usages by default: a vault metadata key seals and opens across one
// session. A caller needing only one passes it. async so a bad length arrives
// as a rejection like every other failure here, rather than as a synchronous
// throw from a function that otherwise returns a promise.
export async function importAeadKey(raw, usages = ['encrypt', 'decrypt']) {
  assertRawKeyLength(raw);
  return crypto.subtle.importKey('raw', raw, 'AES-GCM', false, usages);
}

async function subtleKeyFor(key, usage) {
  // WebCrypto picks the AES variant from the key, so a 16-byte one would
  // quietly produce AES-128-GCM under a header still declaring AES-256-GCM -
  // the agility byte would be a lie. That holds for a CryptoKey somebody
  // imported elsewhere just as much as for raw bytes, so both are checked.
  if (!isRawKey(key)) {
    const { name, length } = key.algorithm || {};
    if (name !== 'AES-GCM' || length !== KEY_LENGTH * 8) {
      throw new Error(`aes-256-gcm needs an AES-GCM ${KEY_LENGTH * 8}-bit key`);
    }
    return key;
  }
  assertRawKeyLength(key);
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
