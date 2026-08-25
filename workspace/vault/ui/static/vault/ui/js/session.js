// The account identity, open for the length of a session and no longer.
//
// Everything it holds lives in this closure: nothing is a property, so a lock
// really does put the keys out of reach rather than merely marking them stale.
// Each extraction follows the same three steps - decrypt into a transient
// buffer, import as a non-extractable key, zero the buffer - because the one
// path that cannot avoid raw bytes (HPKE hands them back whatever happens)
// sets the standard for the ones that could.
var VAULT_SECRET_STORAGE_KEY = 'vault.secret-key';

function VaultUnlockError(reason) {
  var error = new Error('vault unlock failed: ' + reason);
  error.name = 'VaultUnlockError';
  error.reason = reason;
  return error;
}

// WebCrypto imports an Ed25519 PRIVATE key as 'pkcs8' or 'jwk' only, never
// 'raw' - so the bare 32-byte seed this file works with needs the fixed
// prelude in front of it before crypto.subtle can touch it.
var PKCS8_ED25519_PREFIX = Uint8Array.from([
  0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20,
]);

// Deliberately checks only the overflow case (`.set` would otherwise write
// past the fixed 32-byte slot), not exact-32 equality: the recomputed public
// key is a defense-in-depth cross-check, not the seed's only consumer, and a
// too-short value here still fails loudly downstream when Ed25519 rejects the
// malformed PKCS#8 structure.
function pkcs8FromSeed(seed) {
  if (seed.length > 32) throw new Error('Ed25519 seed is ' + seed.length + ' bytes, expected at most 32');
  var out = new Uint8Array(PKCS8_ED25519_PREFIX.length + 32);
  out.set(PKCS8_ED25519_PREFIX, 0);
  out.set(seed, PKCS8_ED25519_PREFIX.length);
  return out;
}

window.VaultSession = (function () {
  var accountUuid = null;
  var signer = null;
  var recipient = null;
  var sigPublicRaw = null;
  var lockCallbacks = [];
  var unlocked = false;

  function zero(buffer) {
    if (buffer && buffer.fill) buffer.fill(0);
  }

  // WebCrypto exports an Ed25519 private key as JWK carrying both halves, and
  // that is the only way to learn the public key the seed implies. The handle
  // is extractable for this one call and dropped immediately; the key that
  // survives the function is the non-extractable one importSigner holds.
  async function publicKeyFromSeed(seed) {
    var V = window.VaultCrypto;
    var pkcs8 = await crypto.subtle.importKey(
      'pkcs8', pkcs8FromSeed(seed), 'Ed25519', true, ['sign']
    );
    var jwk = await crypto.subtle.exportKey('jwk', pkcs8);
    return V.fromBase64Url(jwk.x);
  }

  return {
    isUnlocked: function () { return unlocked; },
    accountUuid: function () { return accountUuid; },
    rememberedSecret: function () {
      return localStorage.getItem(VAULT_SECRET_STORAGE_KEY);
    },
    forgetDevice: function () {
      localStorage.removeItem(VAULT_SECRET_STORAGE_KEY);
    },
    onLock: function (callback) { lockCallbacks.push(callback); },

    unlock: async function (options) {
      var V = window.VaultCrypto;
      var envelope;
      try {
        envelope = await window.VaultApi.fetchEnvelope();
      } catch (err) {
        throw VaultUnlockError(err.status === 404 ? 'identity' : 'network');
      }
      if (envelope.state !== 'active') throw VaultUnlockError('identity');
      accountUuid = envelope.uuid;

      var secretBytes = V.crockfordDecode(options.secretText);
      var amk = await V.deriveAmk({
        password: options.password.normalize('NFC'),
        secretKey: secretBytes,
        salt: V.fromBase64Url(envelope.kdf_salt),
        params: envelope.kdf_params,
      });
      var unwrapKey = await V.hkdf(amk, V.AD.unwrapInfo());
      zero(amk);

      var kexPriv;
      var sigSeed;
      try {
        // The tag failure here IS the wrong-password answer. No request
        // validates it, and the server never learns whether one succeeded.
        kexPriv = await V.open(
          unwrapKey,
          V.fromBase64Url(envelope.wrapped_kex_priv),
          V.AD.kexPrivAd(accountUuid)
        );
        sigSeed = await V.open(
          unwrapKey,
          V.fromBase64Url(envelope.wrapped_sig_priv),
          V.AD.sigPrivAd(accountUuid)
        );
      } catch (err) {
        zero(unwrapKey);
        zero(secretBytes);
        throw VaultUnlockError('password');
      }
      // Nothing in v1 needs it again: a rotation re-derives it from the
      // password the user retypes.
      zero(unwrapKey);
      zero(secretBytes);

      var recomputed = await publicKeyFromSeed(sigSeed);
      var served = V.decodePublicKey(V.fromBase64Url(envelope.sig_public));
      if (!V.equalBytes(recomputed, served)) {
        zero(kexPriv);
        zero(sigSeed);
        throw VaultUnlockError('substituted-key');
      }
      sigPublicRaw = recomputed;

      // Verified with the recomputed key, never with the served one: checking
      // a server's signature with the server's own key proves nothing.
      try {
        await V.verifyBytes(
          sigPublicRaw,
          V.AD.kexPubPayload(accountUuid, envelope.kex_public),
          V.fromBase64Url(envelope.sig_over_kex_pub)
        );
      } catch (err) {
        zero(kexPriv);
        zero(sigSeed);
        throw VaultUnlockError('substituted-key');
      }

      signer = await V.importSigner(sigSeed);
      recipient = await V.hpkeRecipient(kexPriv);
      zero(sigSeed);
      zero(kexPriv);

      if (options.remember) {
        localStorage.setItem(VAULT_SECRET_STORAGE_KEY, options.secretText);
      }
      unlocked = true;
    },

    lock: function () {
      if (!unlocked) return;
      unlocked = false;
      signer = null;
      recipient = null;
      sigPublicRaw = null;
      accountUuid = null;
      lockCallbacks.forEach(function (callback) { callback(); });
    },

    sign: async function (payload) {
      if (!unlocked) throw VaultUnlockError('locked');
      var V = window.VaultCrypto;
      return V.toBase64Url(await signer.sign(V.canonicalCbor(payload)));
    },

    openVaultKey: async function (vaultUuid, wrappedKeyB64) {
      if (!unlocked) throw VaultUnlockError('locked');
      var V = window.VaultCrypto;
      var raw = await recipient.open(
        V.AD.vaultKeyInfo(vaultUuid, accountUuid),
        V.fromBase64Url(wrappedKeyB64)
      );
      // The vault key never encrypts anything itself; the metadata key does.
      var metaKey = await V.hkdf(raw, V.AD.vaultMetaInfo(vaultUuid));
      zero(raw);
      // Returns raw metadata-key bytes rather than a CryptoKey: VaultCrypto.open
      // imports its key per call, so there is no CryptoKey form to hand back
      // yet. The bytes live in the caller's local for one decryption - Task 9
      // must zero them. Revisit once aead.js grows a CryptoKey-taking form.
      return metaKey;
    },

    verifyVaultMetadata: async function (payload, signatureB64) {
      if (!unlocked) throw VaultUnlockError('locked');
      var V = window.VaultCrypto;
      await V.verify(
        sigPublicRaw,
        V.canonicalCbor(payload),
        V.fromBase64Url(signatureB64),
        V.VAULT_METADATA_TYPE
      );
    },
  };
})();
