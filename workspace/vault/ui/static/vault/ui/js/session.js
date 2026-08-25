// The account identity, open for the length of a session and no longer.
//
// Everything it holds lives in this closure: nothing is a property, so a lock
// really does put the keys out of reach rather than merely marking them stale.
// Each extraction follows the same three steps - decrypt into a transient
// buffer, import as a non-extractable key, zero the buffer - because the one
// path that cannot avoid raw bytes (HPKE hands them back whatever happens)
// sets the standard for the ones that could.
var VAULT_SECRET_STORAGE_KEY = 'vault.secret-key';

// reason is one of: 'password' (wrong password, or a wrapped key that fails
// to decrypt), 'recovery-key' (the recovery key text itself does not
// decode), 'identity' (no active account envelope, or an unexpected failure
// before the private keys are unwrapped), 'substituted-key' (a spoofed or
// malformed signing key, or an unexpected failure at or after the private
// keys are unwrapped), 'network' (the envelope request itself failed), or
// 'locked' (called before a session is open). cause, when given, is the
// exception this reason was mapped from - kept so a genuine programming
// error stays diagnosable instead of surfacing only as a security-sounding
// reason string.
function VaultUnlockError(reason, cause) {
  var error = new Error('vault unlock failed: ' + reason);
  error.name = 'VaultUnlockError';
  error.reason = reason;
  if (cause !== undefined) error.cause = cause;
  return error;
}

// WebCrypto imports an Ed25519 PRIVATE key as 'pkcs8' or 'jwk' only, never
// 'raw' - so the bare 32-byte seed this file works with needs the fixed
// prelude in front of it before crypto.subtle can touch it.
var PKCS8_ED25519_PREFIX = Uint8Array.from([
  0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20,
]);

function pkcs8FromSeed(seed) {
  if (seed.length !== 32) throw new Error('Ed25519 seed is ' + seed.length + ' bytes, expected 32');
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
        throw VaultUnlockError(err.status === 404 ? 'identity' : 'network', err);
      }
      if (envelope.state !== 'active') throw VaultUnlockError('identity');

      var secretBytes;
      var amk;
      var unwrapKey;
      var kexPriv;
      var sigSeed;
      var recomputed;
      // Held locally, not written into the closure's signer/recipient until
      // every check below has passed: signer and recipient are shared with a
      // session that may already be unlocked, so an aborted second attempt
      // must never overwrite what the live session is using, and a failed
      // first attempt must never leave a half-imported key reachable once
      // unlocked flips true on some later, unrelated success.
      var newSigner;
      var newRecipient;
      // Everything that can still throw once both private keys are unwrapped
      // is working with server-supplied envelope fields (sig_public,
      // kex_public, sig_over_kex_pub) - an unexpected failure there is
      // envelope-shaped, not a credentials problem, so it reports the same
      // way a deliberate substitution does.
      var keysUnwrapped = false;

      try {
        try {
          secretBytes = V.crockfordDecode(options.secretText);
        } catch (err) {
          throw VaultUnlockError('recovery-key', err);
        }
        amk = await V.deriveAmk({
          password: options.password.normalize('NFC'),
          secretKey: secretBytes,
          salt: V.fromBase64Url(envelope.kdf_salt),
          params: envelope.kdf_params,
        });
        unwrapKey = await V.hkdf(amk, V.AD.unwrapInfo());
        zero(amk);

        try {
          // The tag failure here IS the wrong-password answer. No request
          // validates it, and the server never learns whether one succeeded.
          kexPriv = await V.open(
            unwrapKey,
            V.fromBase64Url(envelope.wrapped_kex_priv),
            V.AD.kexPrivAd(envelope.uuid)
          );
          sigSeed = await V.open(
            unwrapKey,
            V.fromBase64Url(envelope.wrapped_sig_priv),
            V.AD.sigPrivAd(envelope.uuid)
          );
        } catch (err) {
          throw VaultUnlockError('password', err);
        }
        keysUnwrapped = true;
        // Nothing in v1 needs it again: a rotation re-derives it from the
        // password the user retypes.
        zero(unwrapKey);
        zero(secretBytes);

        recomputed = await publicKeyFromSeed(sigSeed);
        var served = V.decodePublicKey(V.fromBase64Url(envelope.sig_public));
        if (!V.equalBytes(recomputed, served)) {
          throw VaultUnlockError('substituted-key');
        }

        // Verified with the recomputed key, never with the served one:
        // checking a server's signature with the server's own key proves
        // nothing.
        try {
          await V.verifyBytes(
            recomputed,
            V.AD.kexPubPayload(envelope.uuid, envelope.kex_public),
            V.fromBase64Url(envelope.sig_over_kex_pub)
          );
        } catch (err) {
          throw VaultUnlockError('substituted-key', err);
        }

        newSigner = await V.importSigner(sigSeed);
        newRecipient = await V.hpkeRecipient(kexPriv);
        zero(sigSeed);
        zero(kexPriv);
      } catch (err) {
        // A crash anywhere above must not leave decrypted key material
        // behind, however it got here: zero() no-ops on whatever was never
        // assigned, so this is safe to run unconditionally.
        zero(secretBytes);
        zero(amk);
        zero(unwrapKey);
        zero(kexPriv);
        zero(sigSeed);
        // Nothing to reset here: importSigner/hpkeRecipient wrote to
        // newSigner/newRecipient, not to the closure's signer/recipient, so
        // a failed attempt - first or a later one against an already-live
        // session - never touched what's committed.
        if (err && err.name === 'VaultUnlockError') throw err;
        throw VaultUnlockError(keysUnwrapped ? 'substituted-key' : 'identity', err);
      }

      // Committed only once every check above has passed: a caller reading
      // these mid-verification must never observe a value that later turns
      // out untrusted.
      accountUuid = envelope.uuid;
      sigPublicRaw = recomputed;
      signer = newSigner;
      recipient = newRecipient;
      unlocked = true;

      // Failing to remember the device does not mean failing to unlock it -
      // the session above is already live and must stay that way.
      if (options.remember) {
        try {
          localStorage.setItem(VAULT_SECRET_STORAGE_KEY, options.secretText);
        } catch (err) {
          // Best-effort only; nothing to recover, nothing to zero.
        }
      }
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
      var metaKey;
      try {
        // The vault key never encrypts anything itself; the metadata key does.
        metaKey = await V.hkdf(raw, V.AD.vaultMetaInfo(vaultUuid));
      } finally {
        zero(raw);
      }
      // The caller owns these bytes and must zero them after one decryption.
      // Revisit once aead.js grows a CryptoKey-taking form.
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
