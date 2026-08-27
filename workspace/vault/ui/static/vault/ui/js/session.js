// The account identity, open for the length of a session and no longer.
//
// Everything it holds lives in this closure: nothing is a property, so a lock
// really does put the keys out of reach rather than merely marking them stale.
// Each extraction follows the same three steps - decrypt into a transient
// buffer, import as a non-extractable key, zero the buffer - because the one
// path that cannot avoid raw bytes (HPKE hands them back whatever happens)
// sets the standard for the ones that could.
// Where the recovery key lives between sessions when the box is ticked, in
// both this file and onboarding.js. It is written as plain text, and CodeQL
// says so (js/clear-text-storage-of-sensitive-data) - the trade-off is taken
// knowingly, not overlooked:
//
//   - The key is a secret from the server, which is the adversary this module
//     is built against. Nothing here ever reaches it.
//   - It opens nothing on its own: the KDF needs the master password too, and
//     a script able to read this storage could already keylog that password.
//   - Storing it sealed under a non-extractable key would move the bar rather
//     than raise it - the same script could call decrypt.
//   - It is opt-in, the checkbox label states exactly this, and unticking it
//     erases what a previous unlock wrote.
//
// Anyone tempted to "fix" the alert should change the decision first, not the
// storage.
const VAULT_SECRET_STORAGE_KEY = 'vault.secret-key';

// reason is one of: 'password' (wrong password, or a wrapped key that fails
// to decrypt), 'recovery-key' (the recovery key text itself does not
// decode), 'identity' (no active account envelope, or an unexpected failure
// before the private keys are unwrapped), 'substituted-key' (a spoofed or
// malformed signing key, or an unexpected failure at or after the private
// keys are unwrapped), 'network' (the envelope request itself failed),
// 'throttled' (the envelope request was rate-limited), or 'locked' (called
// before a session is open). cause, when given, is the
// exception this reason was mapped from - kept so a genuine programming
// error stays diagnosable instead of surfacing only as a security-sounding
// reason string.
function VaultUnlockError(reason, cause) {
  const error = new Error('vault unlock failed: ' + reason);
  error.name = 'VaultUnlockError';
  error.reason = reason;
  if (cause !== undefined) error.cause = cause;
  return error;
}

// WebCrypto imports an Ed25519 PRIVATE key as 'pkcs8' or 'jwk' only, never
// 'raw' - so the bare 32-byte seed this file works with needs the fixed
// prelude in front of it before crypto.subtle can touch it.
const PKCS8_ED25519_PREFIX = Uint8Array.from([
  0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20,
]);

function pkcs8FromSeed(seed) {
  if (seed.length !== 32) throw new Error('Ed25519 seed is ' + seed.length + ' bytes, expected 32');
  const out = new Uint8Array(PKCS8_ED25519_PREFIX.length + 32);
  out.set(PKCS8_ED25519_PREFIX, 0);
  out.set(seed, PKCS8_ED25519_PREFIX.length);
  return out;
}

window.vaultSession = (function () {
  let accountUuid = null;
  let signer = null;
  let recipient = null;
  let sigPublicRaw = null;
  let kexPublicRaw = null;
  const lockCallbacks = [];
  const tickCallbacks = [];
  let unlocked = false;
  const IDLE_LOCK_MS = 5 * 60 * 1000;
  let expiresAt = 0;
  let ticker = null;

  function zero(buffer) {
    if (buffer && buffer.fill) buffer.fill(0);
  }

  // WebCrypto exports an Ed25519 private key as JWK carrying both halves, and
  // that is the only way to learn the public key the seed implies. The handle
  // is extractable for this one call and dropped immediately; the key that
  // survives the function is the non-extractable one importSigner holds.
  //
  // The export is the one place this module cannot keep its own promise that
  // no raw secret outlives its buffer: jwk.d is the 32-byte seed base64url'd
  // into a JS string, and strings are immutable, so it cannot be zeroed the
  // way every Uint8Array here is. Dropping the reference is all that is left -
  // it shortens the window to the next collection instead of the page's life.
  async function publicKeyFromSeed(seed) {
    const V = window.vaultCrypto;
    const pkcs8 = await crypto.subtle.importKey(
      'pkcs8', pkcs8FromSeed(seed), 'Ed25519', true, ['sign']
    );
    const jwk = await crypto.subtle.exportKey('jwk', pkcs8);
    const publicRaw = V.fromBase64Url(jwk.x);
    jwk.d = null;
    return publicRaw;
  }

  return {
    isUnlocked: function () { return unlocked; },
    accountUuid: function () { return accountUuid; },
    accountKexPublicRaw: function () { return kexPublicRaw; },
    // Both wrapped for the same reason the write in unlock() is: private
    // browsing and blocked site data throw on access rather than answering
    // null. A throw here would reach vaultApp.init(), which calls this as its
    // first statement - Alpine would abandon the rest of it, and the session
    // would run with no idle timer, no countdown and no lock on tab hide.
    rememberedSecret: function () {
      try {
        return localStorage.getItem(VAULT_SECRET_STORAGE_KEY);
      } catch (err) {
        return null;
      }
    },
    forgetDevice: function () {
      try {
        localStorage.removeItem(VAULT_SECRET_STORAGE_KEY);
      } catch (err) {
        // Nothing readable was stored either, so there is nothing to undo.
      }
    },
    onLock: function (callback) { lockCallbacks.push(callback); },
    // Fired from tick(), once a second, whether or not that tick locked the
    // session - the countdown display has no other reactive hook into this
    // closure, so it reads secondsUntilLock() itself from the callback.
    onTick: function (callback) { tickCallbacks.push(callback); },

    unlock: async function (options) {
      const V = window.vaultCrypto;
      let envelope;
      try {
        envelope = await window.vaultApi.fetchEnvelope();
      } catch (err) {
        // 429 is kept apart from 'network' on purpose: the envelope carries a
        // burst limit and every unlock refetches it, so this is reachable by
        // ordinary use. Telling the user to check their connection would send
        // them after the wrong thing.
        let reason = 'network';
        if (err.status === 404) reason = 'identity';
        else if (err.status === 429) reason = 'throttled';
        throw VaultUnlockError(reason, err);
      }
      if (envelope.state !== 'active') throw VaultUnlockError('identity');

      let secretBytes;
      let amk;
      let unwrapKey;
      let kexPriv;
      let sigSeed;
      let recomputed;
      // Held locally, not written into the closure's signer/recipient until
      // every check below has passed: signer and recipient are shared with a
      // session that may already be unlocked, so an aborted second attempt
      // must never overwrite what the live session is using, and a failed
      // first attempt must never leave a half-imported key reachable once
      // unlocked flips true on some later, unrelated success.
      let newSigner;
      let newRecipient;
      let newKexPublicRaw;
      // Everything that can still throw once both private keys are unwrapped
      // is working with server-supplied envelope fields (sig_public,
      // kex_public, sig_over_kex_pub) - an unexpected failure there is
      // envelope-shaped, not a credentials problem, so it reports the same
      // way a deliberate substitution does.
      let keysUnwrapped = false;

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
        const served = V.decodePublicKey(V.fromBase64Url(envelope.sig_public));
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

        // The server's kex_public, trusted only now: the attestation just
        // above is what vouches for it, checked with the recomputed signing
        // key rather than the served one. Decoding it any earlier - or
        // re-fetching it later - would hand a second chance to the
        // substitution this whole sequence exists to catch.
        newKexPublicRaw = V.decodePublicKey(V.fromBase64Url(envelope.kex_public));
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
      kexPublicRaw = newKexPublicRaw;
      signer = newSigner;
      recipient = newRecipient;
      unlocked = true;
      expiresAt = Date.now() + IDLE_LOCK_MS;

      // Failing to remember - or forget - the device does not mean failing
      // to unlock it - the session above is already live and must stay that
      // way. The else branch matters as much as the if: an unchecked box
      // must actually revoke a key a previous unlock remembered, not just
      // skip writing a new one.
      try {
        if (options.remember) {
          localStorage.setItem(VAULT_SECRET_STORAGE_KEY, options.secretText);
        } else {
          localStorage.removeItem(VAULT_SECRET_STORAGE_KEY);
        }
      } catch (err) {
        // Best-effort only; nothing to recover, nothing to zero.
      }
    },

    lock: function () {
      if (!unlocked) return;
      unlocked = false;
      signer = null;
      recipient = null;
      sigPublicRaw = null;
      kexPublicRaw = null;
      accountUuid = null;
      expiresAt = 0;
      lockCallbacks.forEach(function (callback) { callback(); });
    },

    secondsUntilLock: function () {
      if (!unlocked) return 0;
      return Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
    },
    noteActivity: function () {
      if (unlocked) expiresAt = Date.now() + IDLE_LOCK_MS;
    },
    tick: function () {
      if (unlocked && Date.now() >= expiresAt) this.lock();
      tickCallbacks.forEach(function (callback) { callback(); });
    },
    // Called once by the page controller. The listeners are registered here
    // rather than in the controller so a second component mounting cannot
    // register a second set - they are per session, not per view.
    watchForIdle: function () {
      if (ticker !== null) return;
      const self = this;
      // Never cleared by lock(): tick() no-ops while locked, and the guard
      // above means a later re-unlock reuses this same ticker rather than
      // accumulating a second one - so nothing needs cancelling.
      ticker = setInterval(function () { self.tick(); }, 1000);
      ['pointerdown', 'keydown', 'wheel'].forEach(function (name) {
        document.addEventListener(name, function () { self.noteActivity(); }, {
          passive: true,
        });
      });
      // A hidden tab is a machine the user has walked away from, and pagehide
      // fires where unload does not on mobile Safari.
      document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'hidden') self.lock();
      });
      addEventListener('pagehide', function () { self.lock(); });
    },

    sign: async function (payload) {
      if (!unlocked) throw VaultUnlockError('locked');
      const V = window.vaultCrypto;
      return V.toBase64Url(await signer.sign(V.canonicalCbor(payload)));
    },

    openVaultKey: async function (vaultUuid, wrappedKeyB64) {
      if (!unlocked) throw VaultUnlockError('locked');
      const V = window.vaultCrypto;
      const raw = await recipient.open(
        V.AD.vaultKeyInfo(vaultUuid, accountUuid),
        V.fromBase64Url(wrappedKeyB64)
      );
      let metaKey;
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
      const V = window.vaultCrypto;
      await V.verify(
        sigPublicRaw,
        V.canonicalCbor(payload),
        V.fromBase64Url(signatureB64),
        V.VAULT_METADATA_TYPE
      );
    },
  };
})();
