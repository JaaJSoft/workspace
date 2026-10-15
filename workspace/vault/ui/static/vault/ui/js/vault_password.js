// Changing the master password: the same private keys, sealed again under a
// key derived from the new password. No vault is re-encrypted.
//
// The session cannot do it with what it holds - its keys are non-extractable
// on purpose - so the envelope is opened again from the current password.
// Nothing is sent before the new envelope has been opened back here, and the
// server keeps the old one until an unlock proves the new one: that is why a
// success locks the session and asks for the new password straight away.
//
// Never name this mixin after a password: CodeQL (js/clear-text-storage)
// treats whatever a call to such a name returns as a secret, and once it is
// spread into the root every localStorage write in vault_browser.js is flagged.
window.vaultResealDialogMixin = function vaultResealDialogMixin() {
  return {
    passwordDialogOpen: false,
    passwordChangedNotice: false,

    openPasswordDialog() {
      this.passwordChangedNotice = false;
      this.passwordDialogOpen = true;
    },

    closePasswordDialog() {
      this.passwordDialogOpen = false;
    },

    // Not passwordChanged: the dialog spreads the floor mixin, whose
    // passwordChanged is the debounced breach lookup, and a nested scope would
    // resolve the bare name to whichever one is closer.
    passwordChangeSucceeded() {
      this.passwordDialogOpen = false;
      this.passwordChangedNotice = true;
    },

    // The notice survives a lock on purpose: the lock that follows a change
    // is what puts it on screen.
    clearPasswordDialog() {
      this.passwordDialogOpen = false;
    },
  };
};

window.vaultPasswordChange = function vaultPasswordChange() {
  return {
    ...window.vaultPasswordFloorMixin(),
    currentPassword: '',
    secretText: '',
    // Whether secretText came from this device, decided once at mount as on
    // the unlock form: a remembered key is used without ever being printed,
    // and the gate must not follow secretText, which x-model rewrites on
    // every keystroke.
    secretRemembered: false,
    busy: false,
    error: '',
    // Bumped by destroy(): a submit still awaiting when the dialog went away
    // (a lock tears it down) must not report or lock a second time.
    life: 0,
    // Named apart from the root's generator state (generatorOpen,
    // applyGenerated, trackGenerated): the root's serve the standalone
    // generator and the entry form, with another signature, and a bare name
    // here would shadow them.
    passwordGeneratorOpen: false,
    // Whether the field still belongs to the panel: set by Use, dropped the
    // moment a human types, so a later draw never overwrites what they typed.
    followsGenerator: false,

    init() {
      const remembered = window.vaultSession.rememberedSecret();
      this.secretText = remembered || '';
      this.secretRemembered = !!remembered;
    },

    destroy() {
      this.life += 1;
      this.currentPassword = '';
      this.secretText = '';
      this.password = '';
      this.confirmation = '';
      this.passwordGeneratorOpen = false;
      this.followsGenerator = false;
    },

    passwordGeneratorOptions() {
      return { mode: 'passphrase', words: 6 };
    },

    applyGeneratedPassword(value) {
      this.password = value;
      this.confirmation = value;
      this.followsGenerator = true;
      this.passwordEdited();
      this.passwordChanged();
    },

    trackGeneratedPassword(value) {
      if (this.followsGenerator) this.applyGeneratedPassword(value);
    },

    canSubmit() {
      return !this.busy && this.currentPassword.length > 0
        && this.secretText.length > 0 && this.passwordAcceptable();
    },

    async submit() {
      if (!this.canSubmit()) return;
      const V = window.vaultCrypto;
      const E = window.vaultEnvelope;
      const life = this.life;
      // Read once, before the first await: destroy() empties the fields, and
      // a flow that read them afterwards would seal the keys under an empty
      // password - and send it.
      const currentPassword = this.currentPassword;
      const secretText = this.secretText;
      const secretRemembered = this.secretRemembered;
      const newPassword = this.password;
      const gone = () => life !== this.life;
      this.busy = true;
      this.error = '';
      let secretBytes = null;
      let oldKey = null;
      let newKey = null;
      let keys = null;
      const zeroAll = function () {
        [secretBytes, oldKey, newKey, keys && keys.kexPriv, keys && keys.sigSeed].forEach(function (b) {
          if (b && typeof b.fill === 'function') b.fill(0);
        });
      };
      try {
        const envelope = await window.vaultApi.fetchEnvelope();
        if (gone()) return;
        try {
          secretBytes = V.crockfordDecode(secretText);
        } catch (err) {
          if (gone()) return;
          this.error = 'Your recovery key could not be read. Dashes and case do not matter - check it against your emergency kit.';
          // A remembered key that does not decode is no use hidden: the
          // field comes back, empty, for the emergency kit.
          this.secretText = '';
          this.secretRemembered = false;
          return;
        }
        const salt = V.fromBase64Url(envelope.kdf_salt);
        try {
          oldKey = await E.deriveUnwrapKey(envelope, currentPassword, secretBytes, salt);
          if (gone()) return;
          keys = await E.openKeys(oldKey, envelope, envelope.uuid);
        } catch (err) {
          // The same tag fails for a wrong password and a wrong recovery key;
          // only a key the user typed here can be the culprit.
          if (!gone()) {
            this.error = secretRemembered
              ? 'Current password is incorrect.'
              : 'Current password or recovery key is incorrect.';
          }
          return;
        }
        if (gone()) return;
        const current = V.CURRENT_SUITE.kdf;
        newKey = await E.deriveUnwrapKey(
          { kdf_algo: current.algo, kdf_params: current.params }, newPassword, secretBytes, salt
        );
        if (gone()) return;
        let sealed;
        try {
          sealed = await E.sealAndReread(newKey, keys, envelope.uuid);
        } catch (err) {
          if (!gone()) this.error = 'The new password could not be applied.';
          return;
        }
        // Nothing raw rides along with the request: only the two sealed
        // ciphertexts are needed from here on.
        zeroAll();
        if (gone()) return;
        try {
          await window.vaultApi.rotateEnvelope(E.rotateBody(sealed, envelope.wrapped_kex_priv));
        } catch (err) {
          if (gone()) return;
          this.error = err && err.status === 409
            ? 'Your account changed in another tab. Reload and try again.'
            : 'The password could not be changed. Try again.';
          return;
        }
        if (gone()) return;
        this.$dispatch('vault-password-changed');
        window.vaultSession.lock();
      } catch (err) {
        if (!gone()) this.error = 'The password could not be changed. Try again.';
      } finally {
        zeroAll();
        this.busy = false;
      }
    },
  };
};
