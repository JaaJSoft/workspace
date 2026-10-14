// The account envelope: the two private keys the vault password seals.
//
// Shared by the unlock and the password change, because both open an envelope
// from a password and both write one back - and a write is never sent before
// it has been opened again here. The keys handed in are raw bytes the caller
// zeroes; nothing in this file keeps a reference to one.
window.vaultEnvelope = (function () {
  const FIELDS = ['kdf_algo', 'kdf_params', 'wrapped_kex_priv', 'wrapped_sig_priv'];

  function MismatchError() {
    const error = new Error('the resealed envelope does not open to the same keys');
    error.name = 'VaultEnvelopeMismatch';
    return error;
  }

  function zero(buffer) {
    if (buffer && typeof buffer.fill === 'function') buffer.fill(0);
  }

  // Typed equality: 2.0 or true would compare equal to 2 or 1 and still name
  // another value to a server that types them.
  function sameParams(given, expected) {
    if (!given || typeof given !== 'object' || Array.isArray(given)) return false;
    const keys = Object.keys(expected);
    return Object.keys(given).length === keys.length
      && keys.every(function (key) {
        return Object.hasOwn(given, key) && typeof given[key] === typeof expected[key]
          && given[key] === expected[key];
      });
  }

  function needsDerivation(fields) {
    const current = window.vaultCrypto.CURRENT_SUITE.kdf;
    return fields.kdf_algo !== current.algo || !sameParams(fields.kdf_params, current.params);
  }

  function isStale(fields) {
    const V = window.vaultCrypto;
    if (needsDerivation(fields)) return true;
    return [fields.wrapped_kex_priv, fields.wrapped_sig_priv].some(function (b64) {
      const header = V.decodeCiphertext(V.fromBase64Url(b64));
      return !V.isCurrent('format', header.formatVersion) || !V.isCurrent('aead', header.aeadId);
    });
  }

  function previousOf(envelope) {
    if (!envelope.previous_wrapped_kex_priv) return null;
    const previous = {};
    FIELDS.forEach(function (name) { previous[name] = envelope['previous_' + name]; });
    return previous;
  }

  async function deriveUnwrapKey(fields, password, secretBytes, salt) {
    const V = window.vaultCrypto;
    V.assertAccountKdf(fields.kdf_algo, fields.kdf_params);
    const amk = await V.deriveAmk({
      algo: fields.kdf_algo,
      password: password.normalize('NFC'),
      secretKey: secretBytes,
      salt: salt,
      params: fields.kdf_params,
    });
    try {
      return await V.hkdf(amk, V.AD.unwrapInfo());
    } finally {
      zero(amk);
    }
  }

  // The open error is rethrown untouched: the caller tells a wrong password
  // from an algorithm this build cannot read by its name.
  async function openKeys(unwrapKey, fields, accountUuid) {
    const V = window.vaultCrypto;
    const kexPriv = await V.open(unwrapKey, V.fromBase64Url(fields.wrapped_kex_priv), V.AD.kexPrivAd(accountUuid));
    try {
      const sigSeed = await V.open(unwrapKey, V.fromBase64Url(fields.wrapped_sig_priv), V.AD.sigPrivAd(accountUuid));
      return { kexPriv: kexPriv, sigSeed: sigSeed };
    } catch (err) {
      zero(kexPriv);
      throw err;
    }
  }

  // Sealed under the current suite with the header onboarding writes, then
  // opened again: a writer that got its own output wrong must fail here, on
  // this device, rather than on the server's only copy.
  async function sealAndReread(unwrapKey, keys, accountUuid) {
    const V = window.vaultCrypto;
    const options = { keyVersion: 0, kdfId: V.KDF_DIRECT };
    const kexSealed = await V.seal(unwrapKey, keys.kexPriv, V.AD.kexPrivAd(accountUuid), options);
    const sigSealed = await V.seal(unwrapKey, keys.sigSeed, V.AD.sigPrivAd(accountUuid), options);
    const sealed = {
      wrapped_kex_priv: V.toBase64Url(kexSealed),
      wrapped_sig_priv: V.toBase64Url(sigSealed),
    };
    let reread;
    try {
      reread = await openKeys(unwrapKey, sealed, accountUuid);
    } catch (err) {
      throw MismatchError();
    }
    const same = V.equalBytes(reread.kexPriv, keys.kexPriv) && V.equalBytes(reread.sigSeed, keys.sigSeed);
    zero(reread.kexPriv);
    zero(reread.sigSeed);
    if (!same) throw MismatchError();
    return sealed;
  }

  function rotateBody(sealed, expectedWrappedKexPriv) {
    const current = window.vaultCrypto.CURRENT_SUITE.kdf;
    return {
      kdf_algo: current.algo,
      kdf_params: Object.assign({}, current.params),
      wrapped_kex_priv: sealed.wrapped_kex_priv,
      wrapped_sig_priv: sealed.wrapped_sig_priv,
      expected_wrapped_kex_priv: expectedWrappedKexPriv,
    };
  }

  return {
    isStale: isStale,
    needsDerivation: needsDerivation,
    previousOf: previousOf,
    deriveUnwrapKey: deriveUnwrapKey,
    openKeys: openKeys,
    sealAndReread: sealAndReread,
    rotateBody: rotateBody,
  };
})();
