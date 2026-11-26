// Replacing the recovery key: the same private keys, sealed again under a key
// derived from the same password and a new recovery key. No vault is
// re-encrypted, and the server sees nothing but one more envelope.
//
// Two steps, and their order is the point. The new key is drawn, the private
// keys are sealed under it and read back, and the key is put on screen with
// its emergency kit - all before anything is sent. The rotation leaves only
// once the user says the key is saved. A page lost before that has changed
// nothing; a rotation sent first would leave an account whose current key
// nobody ever saw.
//
// The server keeps the envelope this one replaces until an unlock proves the
// new one: that is why a success locks the session and asks for the new key
// straight away, and why the old key goes on opening the account until then.
//
// Never name the root mixin after a secret, for the reason vault_password.js
// gives for its own: CodeQL would flag every localStorage write of the page.
window.vaultRecoveryKeyDialogMixin = function vaultRecoveryKeyDialogMixin() {
  return {
    recoveryKeyDialogOpen: false,
    // '' | 'replaced' | 'discarded'. Both survive a lock on purpose: the lock
    // that follows a replacement puts the first on screen, and a lock that
    // took the dialog down is the case the second exists for.
    recoveryKeyNotice: '',
    // A new key is on screen, and a kit may have been saved with it, while
    // the account still opens with the old one.
    recoveryKeyShown: false,

    openRecoveryKeyDialog() {
      this.recoveryKeyNotice = '';
      this.recoveryKeyDialogOpen = true;
    },

    // Cancel, Escape and a lock all end here. A key that was shown and never
    // sent opens nothing, and a kit saved with it reads exactly like a valid
    // one - so the page says it, where the dialog no longer can.
    closeRecoveryKeyDialog() {
      if (this.recoveryKeyShown) this.recoveryKeyNotice = 'discarded';
      this.recoveryKeyShown = false;
      this.recoveryKeyDialogOpen = false;
    },

    recoveryKeyWasShown() {
      this.recoveryKeyShown = true;
    },

    // A method, not a template string: the unlock gate and the open vault
    // both show it, and one of the two copies would drift.
    recoveryKeyDiscardedText() {
      return gettext('Your recovery key was not replaced: the one you had still works. The new key you were shown opens nothing - discard it, and any emergency kit saved with it.');
    },

    // The gate that follows must not offer the key this page loaded with:
    // the device remembers the new one now, or none.
    recoveryKeyReplaced() {
      this.recoveryKeyShown = false;
      this.recoveryKeyDialogOpen = false;
      this.recoveryKeyNotice = 'replaced';
      this.readRememberedSecret();
    },
  };
};

window.vaultRecoveryKeyChange = function vaultRecoveryKeyChange() {
  return {
    // 'credentials', then 'kit' once a new key is sealed and waiting.
    step: 'credentials',
    currentPassword: '',
    secretText: '',
    // Whether secretText came from this device, decided at mount as on the
    // unlock form: a remembered key is used without ever being printed.
    secretRemembered: false,
    // The new key, as crockfordEncode drew it. On screen from the kit step
    // until the dialog goes away, and nowhere else.
    nextSecretText: '',
    // What prepare() built and replace() sends: the two sealed keys and the
    // envelope they replace. Ciphertext only - nothing raw waits here.
    pending: null,
    acknowledged: false,
    remember: false,
    busy: false,
    error: '',
    // Bumped by destroy(): a step still awaiting when the dialog went away
    // (a lock tears it down) must not report into a dialog that is gone.
    life: 0,

    init() {
      this.readDevice();
      // A device that held the old key holds the new one unless told not to.
      this.remember = this.secretRemembered;
      // Fetched while the password is typed and the keys are derived, so the
      // kit is one click away when the key appears. A failure waits for that
      // click, which tries again and reports.
      window.vaultEmergencyKit.load(this.$root.dataset.kitBundle).catch(function () {});
    },

    destroy() {
      this.life += 1;
      this.currentPassword = '';
      this.secretText = '';
      this.nextSecretText = '';
      this.pending = null;
    },

    readDevice() {
      const remembered = window.vaultSession.rememberedSecret();
      this.secretText = remembered || '';
      this.secretRemembered = !!remembered;
    },

    groupedSecret() {
      return window.vaultEmergencyKit.grouped(this.nextSecretText);
    },

    canPrepare() {
      return !this.busy && this.currentPassword.length > 0 && this.secretText.length > 0;
    },

    // Everything up to the request: open with the current key, seal under a
    // new one, read it back. Sends nothing.
    async prepare() {
      if (!this.canPrepare()) return;
      const V = window.vaultCrypto;
      const E = window.vaultEnvelope;
      const life = this.life;
      // Read once, before the first await: destroy() empties the fields, and
      // a flow that read them afterwards would seal the keys under an empty
      // password.
      const currentPassword = this.currentPassword;
      const secretText = this.secretText;
      const secretRemembered = this.secretRemembered;
      const gone = () => life !== this.life;
      this.busy = true;
      this.error = '';
      let secretBytes = null;
      let nextSecretBytes = null;
      let oldKey = null;
      let newKey = null;
      let keys = null;
      const zeroAll = function () {
        [secretBytes, nextSecretBytes, oldKey, newKey, keys && keys.kexPriv, keys && keys.sigSeed]
          .forEach(function (b) {
            if (b && typeof b.fill === 'function') b.fill(0);
          });
      };
      try {
        // As in the password change: the unlock's own confirm and re-wrap
        // must have landed before the envelope is read, or the rotation
        // names a stale one.
        await window.vaultSession.envelopeWrites();
        if (gone()) return;
        const envelope = await window.vaultApi.fetchEnvelope();
        if (gone()) return;
        try {
          secretBytes = V.crockfordDecode(secretText);
        } catch (err) {
          if (gone()) return;
          this.error = gettext('Your recovery key could not be read. Dashes and case do not matter - check it against your emergency kit.');
          // A remembered key that does not decode is no use hidden: the
          // field comes back, empty, for the emergency kit.
          this.secretText = '';
          this.secretRemembered = false;
          return;
        }
        const salt = V.fromBase64Url(envelope.kdf_salt);
        // The previous envelope is tried too: an old key that still opens
        // the account is exactly the one worth replacing. The rotation then
        // replaces the current envelope and leaves the occupied previous
        // slot as it is.
        let opened;
        try {
          ({ opened, unwrapKey: oldKey, keys } = await E.openWithFallback(
            envelope, currentPassword, secretBytes, salt
          ));
        } catch (err) {
          if (gone()) return;
          if (err && err.name === 'VaultEnvelopeWrongPassword') {
            // The same tag fails for a wrong password and a wrong recovery
            // key; only a key the user typed here can be the culprit.
            this.error = secretRemembered
              ? gettext('Master password is incorrect.')
              : gettext('Master password or recovery key is incorrect.');
            return;
          }
          if (err && err.name === 'UnsupportedAlgorithmError') {
            this.error = gettext('This vault was saved by a newer version of the app. Reload the page to update it.');
            return;
          }
          throw err;
        }
        if (gone()) return;
        const drawn = V.randomBytes(32);
        const nextSecretText = V.crockfordEncode(drawn);
        drawn.fill(0);
        // Derived from the key as the kit spells it, not from the bytes it
        // was drawn as: what gets sealed under is, by construction, what the
        // user is about to save and type back.
        nextSecretBytes = V.crockfordDecode(window.vaultEmergencyKit.grouped(nextSecretText));
        const current = V.CURRENT_SUITE.kdf;
        newKey = await E.deriveUnwrapKey(
          { kdf_algo: current.algo, kdf_params: current.params }, currentPassword, nextSecretBytes, salt
        );
        if (gone()) return;
        let sealed;
        try {
          sealed = await E.sealAndReread(newKey, keys, envelope.uuid);
        } catch (err) {
          if (!gone()) this.error = gettext('The new recovery key could not be applied.');
          return;
        }
        if (gone()) return;
        this.pending = {
          sealed: sealed,
          expected: envelope.wrapped_kex_priv,
          // Opening the current envelope just proved it, as an unlock would:
          // confirmed first, it is what the previous slot keeps through the
          // rotation, rather than an older envelope still waiting there.
          confirmFirst: opened === 'current' && E.previousOf(envelope) !== null,
        };
        this.nextSecretText = nextSecretText;
        this.currentPassword = '';
        if (!secretRemembered) this.secretText = '';
        this.acknowledged = false;
        this.step = 'kit';
        this.$dispatch('vault-recovery-key-shown');
      } catch (err) {
        if (!gone()) this.error = gettext('Your recovery key could not be replaced. Try again.');
      } finally {
        zeroAll();
        this.busy = false;
      }
    },

    async downloadKit() {
      const life = this.life;
      const kit = { email: this.$root.dataset.email, secretText: this.nextSecretText };
      this.error = '';
      try {
        await window.vaultEmergencyKit.load(this.$root.dataset.kitBundle);
        if (life !== this.life) return;
        window.vaultEmergencyKit.download(kit);
      } catch (err) {
        if (life !== this.life) return;
        this.error = gettext('The emergency kit could not be produced. Write the key down instead, or try again.');
      }
    },

    canReplace() {
      return !this.busy && this.acknowledged && this.pending !== null;
    },

    // Whether the envelope on the server is the one this dialog sealed. A
    // refused or unanswered rotation does not say: the request may have
    // landed and its answer been lost, and calling that key void would have
    // its owner throw away the one that now opens the account.
    async rotationLanded(pending) {
      try {
        const envelope = await window.vaultApi.fetchEnvelope();
        return envelope.wrapped_kex_priv === pending.sealed.wrapped_kex_priv;
      } catch (err) {
        return false;
      }
    },

    // The key on screen opens nothing and never will: back to the first
    // step, where another attempt draws another one.
    withdraw() {
      this.pending = null;
      this.nextSecretText = '';
      this.acknowledged = false;
      this.readDevice();
      this.step = 'credentials';
    },

    async replace() {
      if (!this.canReplace()) return;
      const E = window.vaultEnvelope;
      const life = this.life;
      const pending = this.pending;
      const remember = this.remember;
      const remembered = this.groupedSecret();
      const gone = () => life !== this.life;
      this.busy = true;
      this.error = '';
      try {
        if (pending.confirmFirst) {
          try {
            await window.vaultApi.confirmEnvelope(pending.expected);
          } catch (err) {
            if (!gone()) this.error = gettext('Your recovery key could not be replaced. Try again.');
            return;
          }
          if (gone()) return;
        }
        try {
          await window.vaultApi.rotateEnvelope(E.rotateBody(pending.sealed, pending.expected));
        } catch (err) {
          if (!(await this.rotationLanded(pending))) {
            if (gone()) return;
            if (err && err.status === 409) {
              this.withdraw();
              this.error = gettext('Your account changed in another tab or on another device, so nothing was replaced: the recovery key you had still works. The key shown here opens nothing - discard it, and try again for a new one.');
            } else if (err && err.status === 429) {
              // Its own wording: the limit is counted by the hour, and "try
              // again" would send the user straight back into it.
              this.error = gettext('Too many changes to this account in the last hour, so nothing was replaced yet. Try again later.');
            } else {
              this.error = gettext('Your recovery key could not be replaced. Try again.');
            }
            return;
          }
        }
        // Past this point the account opens with the new key, whether or not
        // this dialog is still on screen - a lock during the request takes
        // the dialog, not the rotation. So nothing below asks gone(), and the
        // page is told on the window, which a detached element still reaches.
        if (remember) window.vaultSession.rememberDevice(remembered);
        else window.vaultSession.forgetDevice();
        window.dispatchEvent(new CustomEvent('vault-recovery-key-replaced'));
        window.vaultSession.lock();
      } catch (err) {
        if (!gone()) this.error = gettext('Your recovery key could not be replaced. Try again.');
      } finally {
        this.busy = false;
      }
    },
  };
};
