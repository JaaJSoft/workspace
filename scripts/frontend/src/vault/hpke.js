import { Aes256Gcm, CipherSuite, HkdfSha256 } from '@hpke/core';
import { DhkemX25519HkdfSha256 } from '@hpke/dhkem-x25519';
import { storedHpkeFormat } from './suites.js';

// mode_base throughout. The aad parameter stays empty - all context binding
// goes through info, which is what keeps a JS and a Python implementation from
// diverging on aad encoding.
const ENC_LENGTH = 32; // DHKEM(X25519) encapsulated key size

// A Uint8Array can be a view into a larger buffer, so handing `.buffer` to the
// KEM would deserialize whatever surrounds the key rather than the key. The
// copy costs 32 bytes and removes the question.
function exactBuffer(bytes) {
  return bytes.slice().buffer;
}

function construction() {
  return new CipherSuite({
    kem: new DhkemX25519HkdfSha256(), kdf: new HkdfSha256(), aead: new Aes256Gcm(),
  });
}

// Every suite the manifest declares for a format is this construction today;
// a second one lands as a branch here plus a manifest entry. The stored suite
// is checked before any HPKE maths runs.
function suiteFor(stored) {
  storedHpkeFormat(stored);
  return construction();
}

// The ephemeral key is drawn per call and cannot be supplied: @hpke/core has no
// override for it, and its `senderKey` parameter is not one - it switches the
// suite to mode_auth, which binds the wrap to a sender identity nothing here
// verifies. Two seals of the same plaintext therefore differ, so parity with
// the reference implementation is asserted by opening its output rather than
// by comparing bytes.
export async function hpkeSeal(recipientPublicRaw, info, plaintext, hpkeSuite) {
  const s = suiteFor(hpkeSuite);
  const pkr = await s.kem.deserializePublicKey(exactBuffer(recipientPublicRaw));
  const sender = await s.createSenderContext({ recipientPublicKey: pkr, info });
  const ciphertext = new Uint8Array(await sender.seal(plaintext, new Uint8Array(0)));
  const out = new Uint8Array(ENC_LENGTH + ciphertext.length);
  out.set(new Uint8Array(sender.enc), 0);
  out.set(ciphertext, ENC_LENGTH);
  return out;
}

async function openWith(s, recipientKey, info, sealed) {
  const recipient = await s.createRecipientContext({
    recipientKey, enc: exactBuffer(sealed.slice(0, ENC_LENGTH)), info,
  });
  return new Uint8Array(await recipient.open(sealed.slice(ENC_LENGTH), new Uint8Array(0)));
}

export async function hpkeOpen(recipientPrivateRaw, info, sealed, hpkeSuite) {
  const s = suiteFor(hpkeSuite);
  const skr = await s.kem.deserializePrivateKey(exactBuffer(recipientPrivateRaw));
  return openWith(s, skr, info, sealed);
}

// The session form: the private key is deserialized once and kept as the
// KEM's own key object, so the caller can zero its transient buffer instead
// of holding raw private key bytes for as long as the vault is open. Only one
// KEM exists, so the key object serves every suite a wrap may name.
export async function hpkeRecipient(recipientPrivateRaw) {
  const skr = await construction().kem.deserializePrivateKey(exactBuffer(recipientPrivateRaw));
  return {
    async open(info, sealed, hpkeSuite) {
      return openWith(suiteFor(hpkeSuite), skr, info, sealed);
    },
  };
}
