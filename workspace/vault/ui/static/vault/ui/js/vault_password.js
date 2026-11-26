// Changing the master password: the same private keys, sealed again under a
// key derived from the new password. No vault is re-encrypted.
//
// The session cannot do it with what it holds - its keys are non-extractable
// on purpose - so the envelope is opened again from the current password.
// Nothing is sent before the new envelope has been opened back here, and the
// server keeps the old one until an unlock proves the new one: that is why a
// success locks the session and asks for the new password straight away. The
// steps themselves are envelope_rotation.js's; this file is the dialog.
//
// Never name this mixin after a password: CodeQL (js/clear-text-storage)
// treats whatever a call to such a name returns as a secret, and once it is
// spread into the root every localStorage write the vault browser makes is flagged.
window.vaultResealDialogMixin = function vaultResealDialogMixin() {
  return {
    passwordDialogOpen: false,
    passwordChangedNotice: false,

    openPasswordDialog() {
      this.passwordChangedNotice = false;
      this.passwordDialogOpen = true;
    },

    // The notice survives a lock on purpose: the lock that follows a change
    // is what puts it on screen.
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

    // A generated master password is copied to be filed somewhere the vault
    // is not - no entry holds it, and once the dialog closes nothing hands it
    // back. The default window clears it before a password manager or a
    // notebook is reached; the export passphrase gets the same time for the
    // same reason.
    masterPasswordClipboardPolicy() {
      return { label: gettext('Master password'), seconds: 180 };
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
      try {
        let rotation;
        try {
          rotation = await window.vaultEnvelopeRotation.prepare(
            { password: currentPassword, secretText: secretText },
            { password: newPassword, secretText: secretText },
            gone
          );
        } catch (err) {
          if (gone()) return;
          if (err && err.name === 'VaultRecoveryKeyUnreadable') {
            this.error = gettext('Your recovery key could not be read. Dashes and case do not matter - check it against your emergency kit.');
            // A remembered key that does not decode is no use hidden: the
            // field comes back, empty, for the emergency kit.
            this.secretText = '';
            this.secretRemembered = false;
            return;
          }
          if (err && err.name === 'VaultEnvelopeWrongPassword') {
            // The same tag fails for a wrong password and a wrong recovery
            // key; only a key the user typed here can be the culprit.
            this.error = secretRemembered
              ? gettext('Current password is incorrect.')
              : gettext('Current password or recovery key is incorrect.');
            return;
          }
          // Not a password problem, and saying so would send the user
          // retyping a password that is right.
          if (err && err.name === 'UnsupportedAlgorithmError') {
            this.error = gettext('This vault was saved by a newer version of the app. Reload the page to update it.');
            return;
          }
          if (err && err.name === 'VaultEnvelopeResealFailed') {
            this.error = gettext('The new password could not be applied.');
            return;
          }
          throw err;
        }
        if (!rotation || gone()) return;
        try {
          if (!(await window.vaultEnvelopeRotation.commit(rotation, gone))) return;
        } catch (err) {
          if (gone()) return;
          this.error = err && err.status === 409
            ? gettext('Your account changed in another tab. Reload and try again.')
            : gettext('The password could not be changed. Try again.');
          return;
        }
        if (gone()) return;
        this.$dispatch('vault-password-changed');
        window.vaultSession.lock();
      } catch (err) {
        if (!gone()) this.error = gettext('The password could not be changed. Try again.');
      } finally {
        this.busy = false;
      }
    },
  };
};
