// The archive container. Everything before the ciphertext is in the clear
// because it is what tells a reader how to derive - you cannot decrypt to
// learn how to decrypt - and all 50 bytes of it are the seal's associated
// data, so tampering reads as tampering rather than as a wrong passphrase.
window.vaultArchive = (function () {
  const MAGIC = new Uint8Array([0x56, 0x4c, 0x54, 0x41, 0x52, 0x43, 0x48]); // VLTARCH
  const CONTAINER_VERSION = 0x01;
  const KDF_ARGON2ID = 0x01;
  const HEADER_LENGTH = 50;
  const SALT_LENGTH = 32;
  const IV_LENGTH = 12;

  function encodeHeader({ salt, params }) {
    const header = new Uint8Array(HEADER_LENGTH);
    header.set(MAGIC, 0);
    header[7] = CONTAINER_VERSION;
    header[8] = KDF_ARGON2ID;
    const view = new DataView(header.buffer);
    view.setUint32(9, params.m, false);
    view.setUint32(13, params.t, false);
    header[17] = params.p;
    header.set(salt, 18);
    return header;
  }

  async function buildArchive({ tree, passphrase, salt, iv }) {
    const V = window.vaultCrypto;
    const params = V.ARGON2_PARAMS;
    // A fresh salt per export means a fresh key, which means exactly one seal
    // under it: far below the birthday bound the 96-bit nonce relies on. An
    // incremental archive sealing twice under one key would break that.
    const drawnSalt = salt || V.randomBytes(SALT_LENGTH);
    const header = encodeHeader({ salt: drawnSalt, params });
    const key = await V.deriveArchiveKey({ passphrase, salt: drawnSalt, params });
    try {
      const payload = await V.seal(key, V.canonicalCbor(tree), header, {
        iv: iv || V.randomBytes(IV_LENGTH),
        // The archive key is an HKDF output. Left to the default this byte
        // would lie, and the vector would freeze the lie.
        kdfId: V.KDF_HKDF_SHA256,
        keyVersion: 0,
      });
      const out = new Uint8Array(header.length + payload.length);
      out.set(header, 0);
      out.set(payload, header.length);
      return out;
    } finally {
      key.fill(0);
    }
  }

  function archiveFilename(date) {
    return `vault-export-${date.toISOString().slice(0, 10)}.vaultarchive`;
  }

  return {
    encodeHeader: encodeHeader,
    buildArchive: buildArchive,
    archiveFilename: archiveFilename,
    HEADER_LENGTH: HEADER_LENGTH,
  };
})();
