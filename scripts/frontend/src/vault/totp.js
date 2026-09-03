// One-time codes, RFC 6238 over RFC 4226.
//
// Hand-written rather than pulled from npm: HMAC lives in WebCrypto, so what
// is left is a base32 decoder and a URI parser. A library would cost ~10 KB
// gzipped on the unlock path - where every byte is latency on top of Argon2id -
// for sixty lines.
//
// What an entry stores is always a complete otpauth:// URI, never a bare
// secret: it is what every exporter emits, so the import of the export step
// receives its own format rather than having to discard the parameters that
// decide the code.
const BASE32_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
const BASE32_DECODE = new Map([...BASE32_ALPHABET].map((symbol, index) => [symbol, index]));

// The closed catalogue. Anything outside it is refused rather than defaulted:
// a silent fallback turns a service we cannot serve into six wrong digits, and
// wrong digits look exactly like a clock that has drifted.
const ALGORITHMS = { SHA1: 'SHA-1', SHA256: 'SHA-256', SHA512: 'SHA-512' };
const MIN_DIGITS = 6;
const MAX_DIGITS = 8;
const MIN_PERIOD = 1;
const MAX_PERIOD = 300;

export function base32Decode(text) {
  // Grouping and padding are presentation: a service prints the key in blocks
  // so it can be read off a screen, and the user pastes what they see.
  const cleaned = String(text).replace(/[\s-]/g, '').replace(/=+$/, '').toUpperCase();
  if (!cleaned.length) throw new Error('authenticator secret is empty');
  const out = [];
  let value = 0;
  let bits = 0;
  for (const symbol of cleaned) {
    const digit = BASE32_DECODE.get(symbol);
    if (digit === undefined) {
      throw new Error(`illegal base32 character ${symbol} in authenticator secret`);
    }
    value = (value << 5) | digit;
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      out.push((value >> bits) & 0xff);
    }
  }
  // Whatever is left has to be zero padding. Five or more leftover bits is a
  // whole symbol that produced no byte, and non-zero bits are data that was
  // cut off: both mean the secret was truncated, and decoding it anyway would
  // hand back a different key that fails silently every thirty seconds.
  if (bits >= 5 || (value & ((1 << bits) - 1)) !== 0) {
    throw new Error('authenticator secret has trailing bits: it looks truncated');
  }
  return new Uint8Array(out);
}

// The reverse, for showing a stored key rather than reading one. Unpadded:
// base32Decode strips padding on the way in, and every authenticator prints
// the key without it.
export function base32Encode(bytes) {
  let out = '';
  let value = 0;
  let bits = 0;
  for (const byte of new Uint8Array(bytes)) {
    value = (value << 8) | byte;
    bits += 8;
    while (bits >= 5) {
      bits -= 5;
      out += BASE32_ALPHABET[(value >> bits) & 31];
    }
  }
  if (bits) out += BASE32_ALPHABET[(value << (5 - bits)) & 31];
  return out;
}

export function parseOtpauth(text) {
  let url;
  try {
    url = new URL(String(text).trim());
  } catch (err) {
    throw new Error('authenticator key is not a uri');
  }
  if (url.protocol !== 'otpauth:') throw new Error('authenticator key is not an otpauth: uri');
  if (url.host.toLowerCase() !== 'totp') throw new Error('only totp keys are supported');
  const params = url.searchParams;
  const algorithm = (params.get('algorithm') || 'SHA1').toUpperCase();
  if (!Object.prototype.hasOwnProperty.call(ALGORITHMS, algorithm)) {
    throw new Error(`unsupported algorithm ${algorithm}`);
  }
  const digits = Number(params.get('digits') || MIN_DIGITS);
  if (!Number.isInteger(digits) || digits < MIN_DIGITS || digits > MAX_DIGITS) {
    throw new Error(`unsupported digits ${params.get('digits')}`);
  }
  const period = Number(params.get('period') || 30);
  if (!Number.isInteger(period) || period < MIN_PERIOD || period > MAX_PERIOD) {
    throw new Error(`unsupported period ${params.get('period')}`);
  }
  return {
    secret: base32Decode(params.get('secret') || ''),
    algorithm: algorithm,
    hash: ALGORITHMS[algorithm],
    digits: digits,
    period: period,
  };
}

// What gets sealed. A pasted uri is stored verbatim - the signature then covers
// exactly what the user saw - and a bare secret is turned into a well-formed
// uri once, here, so nothing downstream ever has to handle two shapes.
export function normalizeTotpInput(text, { label } = {}) {
  const trimmed = String(text || '').trim();
  if (!trimmed) throw new Error('authenticator secret is empty');
  if (/^otpauth:/i.test(trimmed)) {
    parseOtpauth(trimmed);
    return trimmed;
  }
  const secret = trimmed.replace(/[\s-]/g, '').replace(/=+$/, '').toUpperCase();
  base32Decode(secret);
  // The label is never read back: it exists so the uri an export emits is
  // valid. Percent-encoded because an entry may be named with a slash or a
  // question mark, either of which would otherwise become structure.
  return (
    `otpauth://totp/${encodeURIComponent(label || 'Vault')}`
    + `?secret=${secret}&algorithm=SHA1&digits=6&period=30`
  );
}

// extractable: false, like every other key in this module. What the page holds
// afterwards is a handle javascript cannot read back, so a panel left open
// keeps no secret where the developer tools would show one.
export function importTotpKey({ secret, hash }) {
  return crypto.subtle.importKey('raw', secret, { name: 'HMAC', hash: hash }, false, ['sign']);
}

export async function totpCode(key, { digits, period }, atSeconds) {
  const counter = Math.floor(Math.floor(atSeconds) / period);
  const message = new Uint8Array(8);
  new DataView(message.buffer).setBigUint64(0, BigInt(counter), false);
  const mac = new Uint8Array(await crypto.subtle.sign('HMAC', key, message));
  // RFC 4226 dynamic truncation: the low nibble of the last byte picks the
  // window, and the high bit is masked off so the result never reads as a
  // negative signed integer.
  const offset = mac[mac.length - 1] & 0x0f;
  const truncated =
    ((mac[offset] & 0x7f) << 24)
    | (mac[offset + 1] << 16)
    | (mac[offset + 2] << 8)
    | mac[offset + 3];
  return String(truncated % 10 ** digits).padStart(digits, '0');
}

export function totpSecondsRemaining({ period }, atSeconds) {
  return period - (Math.floor(atSeconds) % period);
}
