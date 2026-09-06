import { argon2id } from 'hash-wasm';

// The only Argon2 build that exposes the `secret` parameter (K). argon2-browser
// is banned for exactly this reason: without K the secret_key would have to be
// concatenated with the password, which makes the two interchangeable to the
// KDF - an attacker who learns one then gets the other's search space free.
export const ARGON2_PARAMS = Object.freeze({ v: '1.3', m: 65536, t: 3, p: 2 });

// Argon2 accepts a K and a salt of any length, so a secret_key one character
// short derives a different AMK instead of failing, and only surfaces later as
// a GCM tag error the UI can report as nothing but a wrong password. The error
// names the length, never the value: a secret_key must not reach a log.
const SECRET_KEY_LENGTH = 32;
const SALT_LENGTH = 32;

export async function deriveAmk({ password, secretKey, salt, params = ARGON2_PARAMS }) {
  for (const [name, value, expected] of [
    ['secret_key', secretKey, SECRET_KEY_LENGTH],
    ['salt', salt, SALT_LENGTH],
  ]) {
    if (value.length !== expected) {
      throw new Error(`${name} is ${value.length} bytes, expected ${expected}`);
    }
  }
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

// Argon2 parameters travel in the archive's public header, so a hand-crafted
// file chooses them. Unbounded, m = 4 GiB kills the tab before any error is
// raised. Widening these later accepts more archives; narrowing would reject
// one already written, so this table only ever grows outwards.
export const ARCHIVE_ARGON2_BOUNDS = Object.freeze({
  m: { min: 8192, max: 1048576 },
  t: { min: 1, max: 10 },
  p: { min: 1, max: 4 },
});

export function assertArchiveParams({ m, t, p }) {
  for (const [name, value] of [['m', m], ['t', t], ['p', p]]) {
    const { min, max } = ARCHIVE_ARGON2_BOUNDS[name];
    if (!Number.isInteger(value) || value < min || value > max) {
      throw new Error(`archive ${name} is ${value}, outside [${min}, ${max}]`);
    }
  }
}

const ARCHIVE_KEY_INFO = new TextEncoder().encode('v1|archive-key');

// No `secret` parameter: an export has no secret_key, which is exactly why an
// archive is weaker than the account it came from and why the passphrase has
// to carry real entropy. deriveAmk cannot stand in - it requires a 32-byte K
// and throws without one.
export async function deriveArchiveKey({ passphrase, salt, params = ARGON2_PARAMS }) {
  if (salt.length !== SALT_LENGTH) {
    throw new Error(`salt is ${salt.length} bytes, expected ${SALT_LENGTH}`);
  }
  assertArchiveParams(params);
  const hex = await argon2id({
    password: new TextEncoder().encode(passphrase.normalize('NFC')),
    salt,
    parallelism: params.p,
    iterations: params.t,
    memorySize: params.m,
    hashLength: 32,
    outputType: 'hex',
  });
  const ikm = Uint8Array.from(hex.match(/../g).map((byte) => parseInt(byte, 16)));
  try {
    // The key that encrypts is always an HKDF output - the one rule the spec
    // states without exception - and this step leaves room for a second key
    // later without touching the KDF.
    return await hkdf(ikm, ARCHIVE_KEY_INFO);
  } finally {
    ikm.fill(0);
  }
}

export async function hkdf(ikm, info, length = 32) {
  const key = await crypto.subtle.importKey('raw', ikm, 'HKDF', false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits(
    { name: 'HKDF', hash: 'SHA-256', salt: HKDF_SALT, info }, key, length * 8
  );
  return new Uint8Array(bits);
}
