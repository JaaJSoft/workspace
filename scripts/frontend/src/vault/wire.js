// The six-byte ciphertext header. HPKE-wrapped vault keys do NOT use this
// layout: they carry HPKE's own framed output, their agility living in
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

export function encodeCiphertext({ aeadId, kdfId, keyVersion, iv, ciphertext }) {
  // Integer-ness is checked, not assumed: the two header bytes are written
  // with >> and &, which silently truncate a float instead of failing, where
  // the reference implementation raises. A version written as 1 when the caller
  // meant 1.5 is a ciphertext nothing will ever open with the right key.
  if (!Number.isInteger(keyVersion) || keyVersion < 0 || keyVersion > 0xffff) {
    throw new Error(`key_version ${keyVersion} does not fit in two bytes`);
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
  return {
    formatVersion: raw[0],
    aeadId,
    kdfId: raw[2],
    keyVersion: (raw[3] << 8) | raw[4],
    iv: raw.slice(HEADER_LENGTH, HEADER_LENGTH + ivLen),
    ciphertext: raw.slice(HEADER_LENGTH + ivLen),
  };
}
