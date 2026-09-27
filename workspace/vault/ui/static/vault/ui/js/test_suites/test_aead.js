// AES-256-CTR then HMAC-SHA256, registered as AEAD 0xF0. It exists to prove
// that every reader dispatches on the header; nothing in production loads it,
// and the server refuses its id outside VAULT_TEST_SUITES.
//
// It enters through registerAead, the door a real algorithm would use, so what
// the tests prove is the production path. Its working keys are HKDF children
// of the 32-byte key, never the key itself: the same bytes also key AES-GCM.
(function () {
  const V = window.vaultCrypto;
  const TAG_LENGTH = 32;
  const encoder = new TextEncoder();

  async function workingKeys(raw, usages) {
    const encRaw = await V.hkdf(raw, encoder.encode('test-aead|enc'));
    const macRaw = await V.hkdf(raw, encoder.encode('test-aead|mac'));
    try {
      const wantsEncrypt = usages.includes('encrypt');
      return {
        enc: await crypto.subtle.importKey('raw', encRaw, 'AES-CTR', false, usages),
        mac: await crypto.subtle.importKey('raw', macRaw, { name: 'HMAC', hash: 'SHA-256' }, false,
          wantsEncrypt ? ['sign', 'verify'] : ['verify']),
      };
    } finally {
      encRaw.fill(0);
      macRaw.fill(0);
    }
  }

  function macInput(iv, ciphertext, ad) {
    const out = new Uint8Array(8 + ad.length + iv.length + ciphertext.length);
    new DataView(out.buffer).setBigUint64(0, BigInt(ad.length), false);
    out.set(ad, 8);
    out.set(iv, 8 + ad.length);
    out.set(ciphertext, 8 + ad.length + iv.length);
    return out;
  }

  V.registerAead(0xf0, {
    ivLength: 16,
    importKey: workingKeys,
    async seal(handle, iv, plaintext, ad) {
      // length 128: the whole block is the counter, as in the reference's CTR mode.
      const ciphertext = new Uint8Array(await crypto.subtle.encrypt(
        { name: 'AES-CTR', counter: iv, length: 128 }, handle.enc, plaintext));
      const tag = new Uint8Array(await crypto.subtle.sign('HMAC', handle.mac, macInput(iv, ciphertext, ad)));
      const out = new Uint8Array(ciphertext.length + TAG_LENGTH);
      out.set(ciphertext, 0);
      out.set(tag, ciphertext.length);
      return out;
    },
    async open(handle, iv, sealed, ad) {
      if (sealed.length < TAG_LENGTH) throw new Error('test aead: ciphertext shorter than its tag');
      const ciphertext = sealed.slice(0, sealed.length - TAG_LENGTH);
      const tag = sealed.slice(sealed.length - TAG_LENGTH);
      const ok = await crypto.subtle.verify('HMAC', handle.mac, tag, macInput(iv, ciphertext, ad));
      if (!ok) throw new Error('test aead: tag does not verify');
      return new Uint8Array(await crypto.subtle.decrypt(
        { name: 'AES-CTR', counter: iv, length: 128 }, handle.enc, ciphertext));
    },
  });
})();
