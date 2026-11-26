// A rotation its owner asks for: the same two private keys, sealed again under
// other credentials. A password change and a recovery key replacement are both
// this, and differ only in which half of the credentials is new.
//
// account_envelope.js opens and seals; this file is the order those steps run
// in around the network, written once because every line of it is a way to
// lose an account. Two calls, so a caller can put a screen between them:
// prepare() reads and seals and sends nothing, commit() sends.
//
// Every raw buffer is born and zeroed in here. Credentials come in as text and
// what goes out is ciphertext.
window.vaultEnvelopeRotation = (function () {
  // The opening key does not decode: nothing was derived, and the caller has
  // a field to put back on screen.
  function UnreadableKeyError(cause) {
    const error = new Error('the recovery key does not decode');
    error.name = 'VaultRecoveryKeyUnreadable';
    error.cause = cause;
    return error;
  }

  // The keys could not be sealed under the new credentials, or did not read
  // back as themselves. Either way nothing may be sent.
  function ResealError(cause) {
    const error = new Error('the keys could not be sealed under the new credentials');
    error.name = 'VaultEnvelopeResealFailed';
    error.cause = cause;
    return error;
  }

  function zero(buffer) {
    if (buffer && typeof buffer.fill === 'function') buffer.fill(0);
  }

  // opening and sealing are { password, secretText }. cancelled() is asked
  // after every await: a caller that went away (a lock tears a dialog down)
  // must not have its keys held through another derivation.
  //
  // Resolves to { sealed, expected, confirmFirst }, or to null once cancelled.
  // Rejects with VaultRecoveryKeyUnreadable, VaultEnvelopeWrongPassword,
  // UnsupportedAlgorithmError, VaultEnvelopeResealFailed, or whatever else
  // broke, untouched.
  async function prepare(opening, sealing, cancelled) {
    const V = window.vaultCrypto;
    const E = window.vaultEnvelope;
    let openingBytes = null;
    let sealingBytes = null;
    let oldKey = null;
    let newKey = null;
    let keys = null;
    try {
      // The unlock's confirm and re-wrap run in the background: read before
      // they land, the envelope is already stale when the rotation names it,
      // and the server answers a 409 that blames another tab.
      await window.vaultSession.envelopeWrites();
      if (cancelled()) return null;
      const envelope = await window.vaultApi.fetchEnvelope();
      if (cancelled()) return null;
      try {
        openingBytes = V.crockfordDecode(opening.secretText);
      } catch (err) {
        throw UnreadableKeyError(err);
      }
      // Decoded from text even when the key is new: what the keys are sealed
      // under is then, by construction, what its owner will type back.
      sealingBytes = sealing.secretText === opening.secretText
        ? openingBytes
        : V.crockfordDecode(sealing.secretText);
      const salt = V.fromBase64Url(envelope.kdf_salt);
      // The previous envelope is tried too: credentials that still open the
      // account are still its owner's to replace. The rotation then replaces
      // only the current envelope, and the server keeps an occupied previous
      // slot as it is.
      let opened;
      ({ opened, unwrapKey: oldKey, keys } = await E.openWithFallback(
        envelope, opening.password, openingBytes, salt
      ));
      if (cancelled()) return null;
      const current = V.CURRENT_SUITE.kdf;
      newKey = await E.deriveUnwrapKey(
        { kdf_algo: current.algo, kdf_params: current.params }, sealing.password, sealingBytes, salt
      );
      if (cancelled()) return null;
      let sealed;
      try {
        sealed = await E.sealAndReread(newKey, keys, envelope.uuid);
      } catch (err) {
        throw ResealError(err);
      }
      return {
        sealed: sealed,
        expected: envelope.wrapped_kex_priv,
        // Opening the current envelope just proved it, as an unlock would: it
        // is what the previous slot should keep through this rotation. Left
        // unconfirmed (the unlock's confirm lost to a throttle or a closed
        // tab), the slot would keep credentials the user changed away from
        // two rotations ago.
        confirmFirst: opened === 'current' && E.previousOf(envelope) !== null,
      };
    } finally {
      // Nothing raw outlives this call, whichever way it ends: only the two
      // sealed ciphertexts are needed from here on.
      [openingBytes, sealingBytes, oldKey, newKey, keys && keys.kexPriv, keys && keys.sigSeed]
        .forEach(zero);
    }
  }

  // Resolves to true once the rotation was accepted, to false when cancelled()
  // said so before it left. Rejects with the API's error, status attached: a
  // refused confirm stops here rather than rotate away the envelope being
  // replaced and keep an older one.
  async function commit(rotation, cancelled) {
    if (rotation.confirmFirst) {
      await window.vaultApi.confirmEnvelope(rotation.expected);
      if (cancelled()) return false;
    }
    await window.vaultApi.rotateEnvelope(
      window.vaultEnvelope.rotateBody(rotation.sealed, rotation.expected)
    );
    return true;
  }

  return { prepare: prepare, commit: commit };
})();
