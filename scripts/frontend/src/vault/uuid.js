// UUIDv7, because the identifier is the row's primary key and a v4 scatters
// inserts across the index. crypto.randomUUID() only mints v4, so the
// timestamp prefix is written here; the remaining 74 bits come from the
// CSPRNG on the first call in a millisecond, and increment from there for
// every following call in the same millisecond - no entropy is home-rolled,
// only the layout.
import { randomBytes } from './encoding.js';

const RAND_BITS = 74n;
const COUNTER_MASK = (1n << RAND_BITS) - 1n;

// Module state, not per-call: two calls landing in the same millisecond must
// still sort, which pure random bits cannot promise on their own (RFC 9562
// "Monotonic Random", method 3). Whichever tab or worker calls this first in
// a given millisecond wins the draw; every following call in that millisecond
// increments it instead of redrawing, so ties never happen.
let lastMillis = -1;
let lastCounter = 0n;

function randomCounter() {
  const drawn = randomBytes(10);
  let value = 0n;
  for (const byte of drawn) value = (value << 8n) | BigInt(byte);
  return value & COUNTER_MASK;
}

export function uuidV7() {
  const millis = Date.now();
  const counter = millis === lastMillis ? (lastCounter + 1n) & COUNTER_MASK : randomCounter();
  lastMillis = millis;
  lastCounter = counter;

  const bytes = new Uint8Array(16);
  // 48-bit big-endian timestamp, then version 7 and the RFC 4122 variant.
  for (let i = 0; i < 6; i += 1) {
    bytes[i] = Number((BigInt(millis) >> BigInt(8 * (5 - i))) & 0xffn);
  }
  // The 74 counter bits fill every bit the timestamp, version and variant
  // don't already own: 4 in byte 6, 8 in byte 7, 6 in byte 8, 56 across
  // bytes 9-15 - so incrementing the counter never touches a fixed bit.
  bytes[6] = 0x70 | Number((counter >> 70n) & 0x0fn);
  bytes[7] = Number((counter >> 62n) & 0xffn);
  bytes[8] = 0x80 | Number((counter >> 56n) & 0x3fn);
  for (let i = 0; i < 7; i += 1) {
    bytes[9 + i] = Number((counter >> BigInt(8 * (6 - i))) & 0xffn);
  }
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
