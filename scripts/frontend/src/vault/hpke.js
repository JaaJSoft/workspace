import { Aes256Gcm, CipherSuite, HkdfSha256 } from '@hpke/core';
import { DhkemX25519HkdfSha256 } from '@hpke/dhkem-x25519';

// Suite v1, normative: kem 0x0020, kdf 0x0001, aead 0x0002, mode_base. The aad
// parameter stays empty - all context binding goes through info, which is what
// keeps a JS and a Python implementation from diverging on aad encoding.
export const HPKE_SUITE_V1 = Object.freeze({
  kem_id: 0x0020, kdf_id: 0x0001, aead_id: 0x0002, mode: 0x00,
});

const ENC_LENGTH = 32; // DHKEM(X25519) encapsulated key size

// A Uint8Array can be a view into a larger buffer, so handing `.buffer` to the
// KEM would deserialize whatever surrounds the key rather than the key. The
// copy costs 32 bytes and removes the question.
function exactBuffer(bytes) {
  return bytes.slice().buffer;
}

function suite() {
  return new CipherSuite({
    kem: new DhkemX25519HkdfSha256(), kdf: new HkdfSha256(), aead: new Aes256Gcm(),
  });
}

// The ephemeral key is drawn per call and cannot be supplied: @hpke/core has no
// override for it, and its `senderKey` parameter is not one - it switches the
// suite to mode_auth, which binds the wrap to a sender identity nothing here
// verifies. Two seals of the same plaintext therefore differ, so parity with
// the reference implementation is asserted by opening its output rather than
// by comparing bytes.
export async function hpkeSeal(recipientPublicRaw, info, plaintext) {
  const s = suite();
  const pkr = await s.kem.deserializePublicKey(exactBuffer(recipientPublicRaw));
  const sender = await s.createSenderContext({ recipientPublicKey: pkr, info });
  const ciphertext = new Uint8Array(await sender.seal(plaintext, new Uint8Array(0)));
  const out = new Uint8Array(ENC_LENGTH + ciphertext.length);
  out.set(new Uint8Array(sender.enc), 0);
  out.set(ciphertext, ENC_LENGTH);
  return out;
}

export async function hpkeOpen(recipientPrivateRaw, info, sealed) {
  const s = suite();
  const skr = await s.kem.deserializePrivateKey(exactBuffer(recipientPrivateRaw));
  const recipient = await s.createRecipientContext({
    recipientKey: skr, enc: exactBuffer(sealed.slice(0, ENC_LENGTH)), info,
  });
  return new Uint8Array(await recipient.open(sealed.slice(ENC_LENGTH), new Uint8Array(0)));
}

// The session form: the private key is deserialized once and kept as the
// suite's own key object, so the caller can zero its transient buffer instead
// of holding raw private key bytes for as long as the vault is open.
export async function hpkeRecipient(recipientPrivateRaw) {
  const s = suite();
  const skr = await s.kem.deserializePrivateKey(exactBuffer(recipientPrivateRaw));
  return {
    async open(info, sealed) {
      const recipient = await s.createRecipientContext({
        recipientKey: skr, enc: exactBuffer(sealed.slice(0, ENC_LENGTH)), info,
      });
      return new Uint8Array(await recipient.open(sealed.slice(ENC_LENGTH), new Uint8Array(0)));
    },
  };
}
