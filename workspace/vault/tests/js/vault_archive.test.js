// The container's public header is the only thing a reader has before it can
// derive anything, so its layout is the format. These offsets are the spec's.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const SCRIPT = 'workspace/vault/ui/static/vault/ui/js/vault_archive.js';

function withCrypto(overrides = {}) {
  const V = Object.assign({
    ARGON2_PARAMS: { v: '1.3', m: 65536, t: 3, p: 2 },
    KDF_HKDF_SHA256: 0x01,
    randomBytes: (count) => new Uint8Array(count).fill(0xab),
    deriveArchiveKey: async () => new Uint8Array(32).fill(1),
    canonicalCbor: () => new Uint8Array([0xa0]),
    seal: async () => new Uint8Array([9, 9, 9]),
  }, overrides);
  return loadScript(SCRIPT, { vaultCrypto: V });
}

test('the public header is 50 bytes and starts with the magic', () => {
  const ctx = withCrypto();
  const header = ctx.vaultArchive.encodeHeader({
    salt: new Uint8Array(32).fill(5), params: { m: 65536, t: 3, p: 2 },
  });
  assert.equal(header.length, 50);
  assert.equal(Buffer.from(header.slice(0, 7)).toString('ascii'), 'VLTARCH');
  assert.equal(header[7], 0x01, 'container version');
  assert.equal(header[8], 0x01, 'kdf id: argon2id');
});

test('the header carries the Argon2 parameters big-endian', () => {
  const ctx = withCrypto();
  const header = ctx.vaultArchive.encodeHeader({
    salt: new Uint8Array(32), params: { m: 65536, t: 3, p: 2 },
  });
  const view = new DataView(header.buffer, header.byteOffset, header.byteLength);
  assert.equal(view.getUint32(9, false), 65536);
  assert.equal(view.getUint32(13, false), 3);
  assert.equal(header[17], 2);
});

test('the header carries the salt at offset 18', () => {
  const ctx = withCrypto();
  const salt = Uint8Array.from({ length: 32 }, (_, i) => i);
  const header = ctx.vaultArchive.encodeHeader({ salt, params: { m: 65536, t: 3, p: 2 } });
  assert.deepStrictEqual(Array.from(header.slice(18, 50)), Array.from(salt));
});

test('the whole public header is the associated data of the seal', async () => {
  // Not decoration: it turns a flipped byte into "this archive has been
  // altered" instead of "wrong passphrase", which is the difference between
  // a user who retypes forever and one who knows the file is broken.
  let seenAd = null;
  const ctx = withCrypto({
    seal: async (key, plaintext, associatedData) => {
      seenAd = associatedData;
      return new Uint8Array([1]);
    },
  });
  const bytes = await ctx.vaultArchive.buildArchive({
    tree: { format: 'vault-archive' }, passphrase: 'x',
  });
  assert.equal(seenAd.length, 50);
  assert.deepStrictEqual(Array.from(seenAd), Array.from(bytes.slice(0, 50)));
});

test('the payload is sealed with the HKDF kdf id and key version zero', async () => {
  // #841 was exactly this byte lying about what produced the key.
  let seenOptions = null;
  const ctx = withCrypto({
    seal: async (key, plaintext, ad, options) => {
      seenOptions = options;
      return new Uint8Array([1]);
    },
  });
  await ctx.vaultArchive.buildArchive({ tree: {}, passphrase: 'x' });
  assert.equal(seenOptions.kdfId, 0x01);
  assert.equal(seenOptions.keyVersion, 0);
});

test('the filename carries the export date', () => {
  const ctx = withCrypto();
  assert.equal(
    ctx.vaultArchive.archiveFilename(new Date(Date.UTC(2026, 8, 6))),
    'vault-export-2026-09-06.vaultarchive'
  );
});
