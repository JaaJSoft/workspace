import { argon2id } from 'hash-wasm';

// The only Argon2 build that exposes the `secret` parameter (K). argon2-browser
// is banned for exactly this reason: without K the secret_key would have to be
// concatenated with the password, which makes the two interchangeable to the
// KDF - an attacker who learns one then gets the other's search space free.
export const ARGON2_PARAMS = Object.freeze({ v: '1.3', m: 65536, t: 3, p: 2 });

export async function deriveAmk({ password, secretKey, salt, params = ARGON2_PARAMS }) {
  // NFC applies to the KDF input, not just to the length check: the same
  // password typed on two keyboards must open the same vault.
  const passwordInput = new TextEncoder().encode(password.normalize('NFC'));
  const hex = await argon2id({
    password: passwordInput,
    salt,
    secret: secretKey,
    parallelism: params.p,
    iterations: params.t,
    memorySize: params.m,
    hashLength: 32,
    outputType: 'hex',
  });
  return Uint8Array.from(hex.match(/../g).map((byte) => parseInt(byte, 16)));
}

// Salt is 32 zero bytes rather than drawn: the input keying material is
// already a uniformly random key, so a salt buys nothing and a drawn one
// would have to be stored and shipped with every derivation.
const HKDF_SALT = new Uint8Array(32);

export async function hkdf(ikm, info, length = 32) {
  const key = await crypto.subtle.importKey('raw', ikm, 'HKDF', false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits(
    { name: 'HKDF', hash: 'SHA-256', salt: HKDF_SALT, info }, key, length * 8
  );
  return new Uint8Array(bits);
}
