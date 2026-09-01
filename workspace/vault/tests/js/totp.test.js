// One-time codes, against the vectors RFC 6238 publishes in appendix B.
//
// The three modes use three different seeds: the ASCII string "12345678901234567890"
// repeated up to the digest length - 20 bytes for SHA-1, 32 for SHA-256, 64 for
// SHA-512. Using one seed for all three is the classic way to fail this table.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript(
  'workspace/vault/ui/static/vault/ui/js/vendor/vault-crypto.js',
  {
    crypto: globalThis.crypto,
    TextEncoder: globalThis.TextEncoder,
    TextDecoder: globalThis.TextDecoder,
    URL: globalThis.URL,
    URLSearchParams: globalThis.URLSearchParams,
    btoa: globalThis.btoa,
    atob: globalThis.atob,
  }
);
const V = ctx.vaultCrypto;

const SEED = '12345678901234567890';
const seedFor = (bytes) => SEED.repeat(Math.ceil(bytes / SEED.length)).slice(0, bytes);
const SEEDS = { SHA1: seedFor(20), SHA256: seedFor(32), SHA512: seedFor(64) };

// RFC 6238 appendix B: T0 = 0, period = 30, 8 digits.
const RFC_VECTORS = [
  { at: 59, SHA1: '94287082', SHA256: '46119246', SHA512: '90693936' },
  { at: 1111111109, SHA1: '07081804', SHA256: '68084774', SHA512: '25091201' },
  { at: 1111111111, SHA1: '14050471', SHA256: '67062674', SHA512: '99943326' },
  { at: 1234567890, SHA1: '89005924', SHA256: '91819424', SHA512: '93441116' },
  { at: 2000000000, SHA1: '69279037', SHA256: '90698825', SHA512: '38618901' },
  { at: 20000000000, SHA1: '65353130', SHA256: '77737706', SHA512: '47863826' },
];

const HASHES = { SHA1: 'SHA-1', SHA256: 'SHA-256', SHA512: 'SHA-512' };

test('the RFC 6238 vectors replay exactly', async () => {
  for (const vector of RFC_VECTORS) {
    for (const algorithm of ['SHA1', 'SHA256', 'SHA512']) {
      const secret = new TextEncoder().encode(SEEDS[algorithm]);
      const key = await V.importTotpKey({ secret: secret, hash: HASHES[algorithm] });
      const code = await V.totpCode(key, { digits: 8, period: 30 }, vector.at);
      assert.equal(code, vector[algorithm], `${algorithm} at t=${vector.at}`);
    }
  }
});

test('the imported key cannot be read back', async () => {
  const key = await V.importTotpKey({
    secret: new TextEncoder().encode(SEEDS.SHA1), hash: 'SHA-1',
  });
  assert.equal(key.extractable, false);
  await assert.rejects(() => crypto.subtle.exportKey('raw', key));
});

test('a six-digit code is padded rather than shortened', async () => {
  // The dynamic truncation can land on a value below 100000, and a code shown
  // as five digits is a code the user types wrong.
  const secret = new TextEncoder().encode(SEEDS.SHA1);
  const key = await V.importTotpKey({ secret: secret, hash: 'SHA-1' });
  for (let at = 0; at < 30 * 400; at += 30) {
    const code = await V.totpCode(key, { digits: 6, period: 30 }, at);
    assert.match(code, /^[0-9]{6}$/, `t=${at}`);
  }
});

test('base32 accepts what a service actually prints', () => {
  const expected = V.base32Decode('JBSWY3DPEHPK3PXP');
  for (const variant of [
    'jbswy3dpehpk3pxp',
    'JBSW Y3DP EHPK 3PXP',
    'JBSW-Y3DP-EHPK-3PXP',
    'JBSWY3DPEHPK3PXP======',
  ]) {
    assert.deepStrictEqual(Array.from(V.base32Decode(variant)), Array.from(expected), variant);
  }
});

test('base32 refuses rather than skips', () => {
  assert.throws(() => V.base32Decode('JBSWY3DP!HPK3PXP'), /illegal/i);
  assert.throws(() => V.base32Decode(''), /empty/i);
  // A single leftover symbol carries five bits that belong to no byte: the
  // secret was truncated, and decoding it would silently produce another key.
  assert.throws(() => V.base32Decode('JBSWY3DPE'), /trailing/i);
});

test('an otpauth uri yields its parameters', () => {
  const parsed = V.parseOtpauth(
    'otpauth://totp/Example:ada@example.com?secret=JBSWY3DPEHPK3PXP&issuer=Example'
      + '&algorithm=SHA256&digits=8&period=60'
  );
  assert.equal(parsed.algorithm, 'SHA256');
  assert.equal(parsed.hash, 'SHA-256');
  assert.equal(parsed.digits, 8);
  assert.equal(parsed.period, 60);
  assert.deepStrictEqual(
    Array.from(parsed.secret), Array.from(V.base32Decode('JBSWY3DPEHPK3PXP'))
  );
});

test('the defaults are the ones every service omits', () => {
  const parsed = V.parseOtpauth('otpauth://totp/Ada?secret=JBSWY3DPEHPK3PXP');
  assert.equal(parsed.algorithm, 'SHA1');
  assert.equal(parsed.digits, 6);
  assert.equal(parsed.period, 30);
});

test('every departure from the catalogue is a refusal, never a default', () => {
  const cases = [
    ['not a uri at all', /uri/i],
    ['https://example.com/?secret=JBSWY3DPEHPK3PXP', /otpauth/i],
    ['otpauth://hotp/Ada?secret=JBSWY3DPEHPK3PXP', /totp/i],
    ['otpauth://totp/Ada?secret=JBSWY3DPEHPK3PXP&algorithm=MD5', /algorithm/i],
    ['otpauth://totp/Ada?secret=JBSWY3DPEHPK3PXP&digits=5', /digits/i],
    ['otpauth://totp/Ada?secret=JBSWY3DPEHPK3PXP&digits=9', /digits/i],
    ['otpauth://totp/Ada?secret=JBSWY3DPEHPK3PXP&digits=6.5', /digits/i],
    ['otpauth://totp/Ada?secret=JBSWY3DPEHPK3PXP&period=0', /period/i],
    ['otpauth://totp/Ada?secret=JBSWY3DPEHPK3PXP&period=301', /period/i],
    ['otpauth://totp/Ada', /base32|empty/i],
  ];
  for (const [uri, pattern] of cases) {
    assert.throws(() => V.parseOtpauth(uri), pattern, uri);
  }
});

test('a pasted uri is stored exactly as it was given', () => {
  const uri = 'otpauth://totp/Ada?secret=JBSWY3DPEHPK3PXP&digits=8';
  assert.equal(V.normalizeTotpInput(uri, { label: 'ignored' }), uri);
});

test('a bare secret becomes a well-formed uri with the defaults', () => {
  const uri = V.normalizeTotpInput('jbsw y3dp ehpk 3pxp', { label: 'Bank / Ada' });
  const parsed = V.parseOtpauth(uri);
  assert.equal(parsed.algorithm, 'SHA1');
  assert.equal(parsed.digits, 6);
  assert.equal(parsed.period, 30);
  // The label is percent-encoded so the slash does not become a path segment
  // the next parser reads as structure.
  assert.ok(uri.includes(encodeURIComponent('Bank / Ada')), uri);
});

test('a secret that is not base32 is refused at the door', () => {
  assert.throws(() => V.normalizeTotpInput('not base32!', { label: 'Ada' }), /illegal/i);
  assert.throws(() => V.normalizeTotpInput('   ', { label: 'Ada' }), /empty/i);
});

test('the remaining validity counts down to the period boundary', () => {
  assert.equal(V.totpSecondsRemaining({ period: 30 }, 0), 30);
  assert.equal(V.totpSecondsRemaining({ period: 30 }, 1), 29);
  assert.equal(V.totpSecondsRemaining({ period: 30 }, 29), 1);
  assert.equal(V.totpSecondsRemaining({ period: 30 }, 30), 30);
  assert.equal(V.totpSecondsRemaining({ period: 60 }, 61.5), 59);
});
