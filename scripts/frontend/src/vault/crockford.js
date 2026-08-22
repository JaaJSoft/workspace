// Crockford base32 for the recovery secret. The alphabet drops I, L, O and U
// so a hand-transcribed secret survives the 0/O and 1/I confusion, and the
// check symbol turns a slip into an error rather than an unlock failure that
// looks exactly like a wrong password.
//
// It belongs to this bundle rather than the on-demand one: the unlock screen
// has to decode what onboarding printed, and the unlock path never loads the
// second bundle.
const ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
const CHECK = ALPHABET + '*~$=U';
const DECODE = new Map([...ALPHABET].map((symbol, index) => [symbol, index]));
DECODE.set('O', 0);
DECODE.set('I', 1);
DECODE.set('L', 1);

function toBigInt(bytes) {
  let value = 0n;
  for (const byte of bytes) value = (value << 8n) | BigInt(byte);
  return value;
}

export function crockfordEncode(raw) {
  const value = toBigInt(raw);
  // 32 bytes are 256 bits, which is 51.2 symbols: the leading one carries
  // four significant bits, and the decoder truncates back rather than
  // trusting the width.
  const width = Math.ceil((raw.length * 8) / 5);
  let out = '';
  for (let shift = width - 1; shift >= 0; shift--) {
    out += ALPHABET[Number((value >> BigInt(shift * 5)) & 0x1fn)];
  }
  return out + CHECK[Number(value % 37n)];
}

export function crockfordDecode(text) {
  // Hyphens and spaces are grouping, not data: the kit prints the secret in
  // blocks so it can be copied by hand, and a user retyping it keeps them.
  const cleaned = text.replace(/[-\s]/g, '').toUpperCase();
  if (cleaned.length < 2) throw new Error('recovery secret is too short');
  const body = cleaned.slice(0, -1);
  const check = cleaned.slice(-1);
  let value = 0n;
  for (const symbol of body) {
    const digit = DECODE.get(symbol);
    if (digit === undefined) {
      throw new Error(`illegal character ${symbol} in recovery secret`);
    }
    value = (value << 5n) | BigInt(digit);
  }
  if (CHECK.indexOf(check) === -1) throw new Error('illegal check symbol');
  if (BigInt(CHECK.indexOf(check)) !== value % 37n) {
    throw new Error('recovery secret fails its check symbol');
  }
  const length = Math.floor((body.length * 5) / 8);
  const out = new Uint8Array(length);
  for (let i = length - 1; i >= 0; i--) {
    out[i] = Number(value & 0xffn);
    value >>= 8n;
  }
  return out;
}
