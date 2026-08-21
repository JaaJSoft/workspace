// The persisted byte layouts: the six-byte ciphertext header, and the one-byte
// algorithm prefix on a stored public key. HPKE-wrapped vault keys do NOT use
// the header: they carry HPKE's own framed output, their agility living in
// VaultKeyWrap.hpke_suite.
export const FORMAT_VERSION = 0x01;
export const AEAD_AES_256_GCM = 0x01;
export const KDF_DIRECT = 0x00;
export const KDF_HKDF_SHA256 = 0x01;

// iv_len is declared rather than inferred, but it must agree with the AEAD: a
// mismatch is how a decoder gets tricked into slicing at the wrong offset.
const IV_LENGTHS = { [AEAD_AES_256_GCM]: 12 };
const HEADER_LENGTH = 6;

export class UnsupportedVersionError extends Error {}

// One byte in front of every persisted public key, so a second key exchange
// algorithm lands without a data migration. The attestation signs the prefixed
// form: an unsigned label would be the server's to change at will.
export const PUBKEY_ALG_X25519 = 0x01;
// Ed25519 carries its own label even though both keys are 32 raw bytes:
// without it, decodePublicKey would hand a signature key back as a key
// exchange key and nothing downstream would notice.
export const PUBKEY_ALG_ED25519 = 0x02;

// Raw key length per algorithm: a stored key of the wrong size is refused
// rather than truncated.
const PUBKEY_LENGTHS = { [PUBKEY_ALG_X25519]: 32, [PUBKEY_ALG_ED25519]: 32 };

export function encodePublicKey(raw, algId = PUBKEY_ALG_X25519) {
  const expected = PUBKEY_LENGTHS[algId];
  if (expected === undefined) throw new Error(`unknown public key algorithm ${algId}`);
  if (raw.length !== expected) {
    throw new Error(`public key is ${raw.length} bytes, algorithm ${algId} wants ${expected}`);
  }
  const out = new Uint8Array(1 + raw.length);
  out[0] = algId;
  out.set(raw, 1);
  return out;
}

// The KEM never sees the prefix: DHKEM(X25519) deserializes a bare 32-byte key,
// so handing it the stored form would read the label as key material.
export function decodePublicKey(stored) {
  if (stored.length < 1) throw new Error('public key is empty');
  const expected = PUBKEY_LENGTHS[stored[0]];
  if (expected === undefined) {
    throw new Error(`unsupported public key algorithm ${stored[0]}`);
  }
  if (stored.length !== 1 + expected) {
    throw new Error(
      `public key is ${stored.length - 1} bytes, algorithm ${stored[0]} wants ${expected}`
    );
  }
  return stored.slice(1);
}

export function encodeCiphertext({ aeadId, kdfId, keyVersion, iv, ciphertext }) {
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
  const expected = IV_LENGTHS[aeadId];
  if (expected === undefined) throw new Error(`unknown aead_id ${aeadId}`);
  if (iv.length !== expected) {
    throw new Error(`iv is ${iv.length} bytes, aead ${aeadId} wants ${expected}`);
  }
  const out = new Uint8Array(HEADER_LENGTH + iv.length + ciphertext.length);
  out.set([FORMAT_VERSION, aeadId, kdfId, keyVersion >> 8, keyVersion & 0xff, iv.length], 0);
  out.set(iv, HEADER_LENGTH);
  out.set(ciphertext, HEADER_LENGTH + iv.length);
  return out;
}

export function decodeCiphertext(raw) {
  if (raw.length < HEADER_LENGTH) throw new Error('ciphertext shorter than its header');
  if (raw[0] !== FORMAT_VERSION) {
    // Rejected before any parsing at all, so a future layout can never be
    // half-read by an old client.
    throw new UnsupportedVersionError(`unsupported format_version ${raw[0]}`);
  }
  const aeadId = raw[1];
  const ivLen = raw[5];
  if (IV_LENGTHS[aeadId] !== ivLen) {
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
  };
}
