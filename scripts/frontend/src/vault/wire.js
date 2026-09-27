import { CURRENT_SUITE, suiteEntry } from './suites.js';

// The persisted byte layouts: the six-byte ciphertext header, and the one-byte
// algorithm prefix on a stored public key. HPKE-wrapped vault keys do NOT use
// the header: they carry HPKE's own framed output, their agility living in
// VaultKeyWrap.hpke_suite.
export const KDF_DIRECT = 0x00;
export const KDF_HKDF_SHA256 = 0x01;
export const HEADER_LENGTH = 6;

// One byte in front of every persisted public key, so a second key exchange
// algorithm lands without a data migration. The attestation signs the prefixed
// form: an unsigned label would be the server's to change at will. A key of
// the wrong size is refused rather than truncated.
export function encodePublicKey(raw, algId) {
  const entry = suiteEntry('pubkey', algId);
  if (raw.length !== entry.length) {
    throw new Error(`public key is ${raw.length} bytes, algorithm ${algId} wants ${entry.length}`);
  }
  const out = new Uint8Array(1 + raw.length);
  out[0] = algId;
  out.set(raw, 1);
  return out;
}

// The KEM never sees the prefix: DHKEM(X25519) deserializes a bare 32-byte key.
// The usage check is what keeps a signing key from reaching the KEM, or the
// reverse - both are 32 bytes, the label is all that tells them apart. A known
// label of the other usage is a malformed key, not an unsupported one.
export function decodePublicKey(stored, usage) {
  if (stored.length < 1) throw new Error('public key is empty');
  const entry = suiteEntry('pubkey', stored[0]);
  if (entry.usage !== usage) throw new Error(`public key algorithm ${stored[0]} is not a ${usage} key`);
  if (stored.length !== 1 + entry.length) {
    throw new Error(`public key is ${stored.length - 1} bytes, algorithm ${stored[0]} wants ${entry.length}`);
  }
  return stored.slice(1);
}

export function encodeCiphertext({
  formatVersion = CURRENT_SUITE.formatVersion, aeadId, kdfId, keyVersion, iv, ciphertext,
}) {
  suiteEntry('format', formatVersion);
  // Integer-ness is checked, not assumed: every header field is written into a
  // byte array, which silently truncates a float or an out-of-range value
  // instead of failing, where the reference implementation raises. A version
  // written as 1 when the caller meant 1.5 is a ciphertext nothing will ever
  // open with the right key.
  if (!Number.isInteger(keyVersion) || keyVersion < 0 || keyVersion > 0xffff) {
    throw new Error(`key_version ${keyVersion} does not fit in two bytes`);
  }
  for (const [name, id] of [['aead_id', aeadId], ['kdf_id', kdfId]]) {
    if (!Number.isInteger(id) || id < 0 || id > 0xff) {
      throw new Error(`${name} ${id} does not fit in one byte`);
    }
  }
  const expected = suiteEntry('aead', aeadId).iv_length;
  if (iv.length !== expected) {
    throw new Error(`iv is ${iv.length} bytes, aead ${aeadId} wants ${expected}`);
  }
  const out = new Uint8Array(HEADER_LENGTH + iv.length + ciphertext.length);
  out.set([formatVersion, aeadId, kdfId, keyVersion >> 8, keyVersion & 0xff, iv.length], 0);
  out.set(iv, HEADER_LENGTH);
  out.set(ciphertext, HEADER_LENGTH + iv.length);
  return out;
}

export function decodeCiphertext(raw) {
  if (raw.length < HEADER_LENGTH) throw new Error('ciphertext shorter than its header');
  // Format first, before any parsing at all: a future layout must never be
  // half-read by an old client.
  suiteEntry('format', raw[0]);
  const aeadId = raw[1];
  const ivLen = raw[5];
  // iv_len is declared rather than inferred, but it must agree with the AEAD: a
  // mismatch is how a decoder gets tricked into slicing at the wrong offset.
  if (suiteEntry('aead', aeadId).iv_length !== ivLen) {
    throw new Error(`iv_len ${ivLen} is inconsistent with aead_id ${aeadId}`);
  }
  // A truncated buffer would otherwise yield a short iv and an empty
  // ciphertext, and only fail several frames later inside WebCrypto.
  if (raw.length < HEADER_LENGTH + ivLen) {
    throw new Error('ciphertext shorter than its declared iv');
  }
  return {
    formatVersion: raw[0],
    aeadId,
    kdfId: raw[2],
    keyVersion: (raw[3] << 8) | raw[4],
    iv: raw.slice(HEADER_LENGTH, HEADER_LENGTH + ivLen),
    ciphertext: raw.slice(HEADER_LENGTH + ivLen),
    header: raw.slice(0, HEADER_LENGTH),
  };
}
