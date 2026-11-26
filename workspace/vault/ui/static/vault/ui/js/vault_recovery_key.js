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
// The reading, sealing and sending are envelope_rotation.js's, as for the
// password change; this file is the dialog around them.
//
// Never name the root mixin after a secret, for the reason vault_password.js
// gives for its own: CodeQL would flag every localStorage write of the page.
window.vaultRecoveryKeyDialogMixin = function vaultRecoveryKeyDialogMixin() {
  return {
    recoveryKeyDialogOpen: false,
    // '' | 'replaced' | 'discarded' | 'unsettled' | 'landed'. They survive a
    // lock on purpose: the lock that follows a replacement puts the first on
    // screen, and a lock that took the dialog down is what the next two are
    // for.
    recoveryKeyNotice: '',
    // A new key is on screen, and a kit may have been saved with it, while
    // the account still opens with the old one.
    recoveryKeyShown: false,
    // The sealed key of a rotation that was sent and that nobody can vouch
    // for yet: still out, or unanswered with the server out of reach to ask.
    // Ciphertext, so it may wait across a lock for an unlock to compare it
    // with the envelope in place.
    recoveryKeyUnsettled: null,
    // Whether that rotation is still out. Comparing envelopes then proves
    // nothing: the request may land a moment after the read.
    recoveryKeyInFlight: false,

    openRecoveryKeyDialog() {
      this.recoveryKeyNotice = '';
      this.recoveryKeyDialogOpen = true;
    },

    // Cancel, Escape and a lock all end here. A key that was shown and never
    // sent opens nothing, and a kit saved with it reads exactly like a valid
    // one - so the page says it, where the dialog no longer can. A key that
    // was sent with no answer is another matter: it may be the one in place.
    closeRecoveryKeyDialog() {
      if (this.recoveryKeyUnsettled) this.recoveryKeyNotice = 'unsettled';
      else if (this.recoveryKeyShown) this.recoveryKeyNotice = 'discarded';
      this.recoveryKeyShown = false;
      this.recoveryKeyDialogOpen = false;
    },

    recoveryKeyWasShown() {
      this.recoveryKeyShown = true;
    },

    // A method, not template strings: the unlock gate and the open vault
    // both show these, and one of the two copies would drift.
    recoveryKeyNoticeText() {
      if (this.recoveryKeyNotice === 'discarded') {
        return gettext('Your recovery key was not replaced: nothing changed. The new key you were shown opens nothing - discard it, and any emergency kit saved with it.');
      }
      if (this.recoveryKeyNotice === 'unsettled') {
        return gettext('This page could not check whether your recovery key was replaced. Keep the new key and its emergency kit with the old ones for now: your next unlock on this page will say which is in place.');
      }
      if (this.recoveryKeyNotice === 'landed') {
        return gettext('Your recovery key was replaced after all - the answer had been lost on the way. Keep the new key and its emergency kit, and unlock with it from now on: that is what retires the old one.');
      }
      return '';
    },

    // What the dialog knows of the rotation it sent: 'sending' from the
    // moment the request leaves, then 'landed' or 'absent' once the server
    // has said, or 'unknown' when it cannot be asked. Heard on the window,
    // because a lock may have taken the dialog while the request was out and
    // a detached element reaches nothing else.
    recoveryKeyOutcome(outcome, sealedKex) {
      if (outcome === 'landed') {
        this.recoveryKeyReplaced();
        return;
      }
      this.recoveryKeyInFlight = outcome === 'sending';
      this.recoveryKeyUnsettled = outcome === 'absent' ? null : sealedKex;
      // An open dialog says it itself. The notice is for a page the dialog
      // has left, where the lock that took it could only assume the key was
      // never sent.
      if (!this.recoveryKeyDialogOpen) {
        this.recoveryKeyNotice = outcome === 'absent' ? 'discarded' : 'unsettled';
      }
    },

    // The gate that follows must not offer the key this page loaded with:
    // the device remembers the new one now, or none.
    recoveryKeyReplaced() {
      this.recoveryKeyShown = false;
      this.recoveryKeyUnsettled = null;
      this.recoveryKeyInFlight = false;
      this.recoveryKeyDialogOpen = false;
      this.recoveryKeyNotice = 'replaced';
      this.readRememberedSecret();
    },

    // Once a session is open: the notices have had their say, and the server
    // is in reach again for the one question left open.
    recoveryKeyUnlocked() {
      this.recoveryKeyNotice = '';
      const sealedKex = this.recoveryKeyUnsettled;
      if (!sealedKex) return;
      // Still out: its own answer will say, and reading the envelope now
      // could call void a key the request is about to put in place.
      if (this.recoveryKeyInFlight) {
        this.recoveryKeyNotice = 'unsettled';
        return;
      }
      const self = this;
      window.vaultApi.fetchEnvelope().then(function (envelope) {
        // Settled in the meantime, by a dialog opened again.
        if (self.recoveryKeyUnsettled !== sealedKex) return;
        self.recoveryKeyUnsettled = null;
        if (envelope.wrapped_kex_priv !== sealedKex) {
          self.recoveryKeyNotice = 'discarded';
          return;
        }
        // The rotation had landed. A device that just unlocked with the old
        // key must not go on doing so: the new one would never be proved and
        // the old one never retired. The dialog that knew the new key is
        // gone, so the device is left remembering none.
        if (window.vaultSession.openedWithPrevious()) {
          window.vaultSession.forgetDevice();
          self.readRememberedSecret();
        }
        self.recoveryKeyNotice = 'landed';
      }, function () {
        self.recoveryKeyNotice = 'unsettled';
      });
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
    // The rotation prepare() built and replace() sends: the two sealed keys
    // and the envelope they replace. Ciphertext only - nothing raw waits here.
    pending: null,
    acknowledged: false,
    remember: false,
    // A rotation was sent and nobody could say what became of it: the step
    // can no longer tell its reader that nothing has changed.
    outcomeUnknown: false,
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
      try {
        const drawn = V.randomBytes(32);
        const nextSecretText = V.crockfordEncode(drawn);
        drawn.fill(0);
        let rotation;
        try {
          // The new key goes in as the kit spells it, not as the bytes it was
          // drawn as: the keys are sealed under what the user will type back.
          rotation = await window.vaultEnvelopeRotation.prepare(
            { password: currentPassword, secretText: secretText },
            { password: currentPassword, secretText: window.vaultEmergencyKit.grouped(nextSecretText) },
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
              ? gettext('Master password is incorrect.')
              : gettext('Master password or recovery key is incorrect.');
            return;
          }
          if (err && err.name === 'UnsupportedAlgorithmError') {
            this.error = gettext('This vault was saved by a newer version of the app. Reload the page to update it.');
            return;
          }
          if (err && err.name === 'VaultEnvelopeResealFailed') {
            this.error = gettext('The new recovery key could not be applied.');
            return;
          }
          throw err;
        }
        if (!rotation || gone()) return;
        this.pending = rotation;
        this.nextSecretText = nextSecretText;
        this.currentPassword = '';
        if (!secretRemembered) this.secretText = '';
        this.acknowledged = false;
        this.step = 'kit';
        this.$dispatch('vault-recovery-key-shown');
      } catch (err) {
        if (!gone()) this.error = gettext('Your recovery key could not be replaced. Try again.');
      } finally {
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

    // What became of a rotation that was refused or never answered: 'landed'
    // when the envelope in place is the one this dialog sealed, 'absent' when
    // it is another, 'unknown' when the server cannot be asked - the usual
    // company of a request that died on the wire. Calling the last one a
    // failure would have the key's owner throw away what may open the account.
    async rotationOutcome(pending) {
      try {
        const envelope = await window.vaultApi.fetchEnvelope();
        return envelope.wrapped_kex_priv === pending.sealed.wrapped_kex_priv ? 'landed' : 'absent';
      } catch (err) {
        return 'unknown';
      }
    },

    // On the window, which a detached element still reaches: a lock during
    // the request takes the dialog, not the rotation.
    tellPage(outcome, pending) {
      window.dispatchEvent(new CustomEvent('vault-recovery-key-outcome', {
        detail: { outcome: outcome, sealed: pending.sealed.wrapped_kex_priv },
      }));
    },

    // The key on screen opens nothing and never will: back to the first
    // step, where another attempt draws another one.
    withdraw() {
      this.pending = null;
      this.nextSecretText = '';
      this.acknowledged = false;
      this.outcomeUnknown = false;
      this.readDevice();
      this.step = 'credentials';
    },

    async replace() {
      if (!this.canReplace()) return;
      const life = this.life;
      const pending = this.pending;
      const remember = this.remember;
      const remembered = this.groupedSecret();
      const gone = () => life !== this.life;
      this.busy = true;
      this.error = '';
      try {
        // In flight from here. Until the server's word is in, a lock that
        // takes the dialog must not be read as proof that nothing was sent.
        this.tellPage('sending', pending);
        try {
          if (!(await window.vaultEnvelopeRotation.commit(pending, gone))) {
            // The dialog went away before the rotation left.
            this.tellPage('absent', pending);
            return;
          }
        } catch (err) {
          // A refusal does not say what the server holds: an earlier attempt
          // may have landed with its answer lost. So the server is asked.
          const outcome = await this.rotationOutcome(pending);
          if (outcome !== 'landed') {
            this.tellPage(outcome, pending);
            if (gone()) return;
            this.outcomeUnknown = outcome === 'unknown';
            if (outcome === 'unknown') {
              this.error = gettext('This page could not check whether your recovery key was replaced. Keep this key and try again.');
            } else if (err && err.status === 409) {
              this.withdraw();
              this.error = gettext('Your account changed in another tab or on another device, so this attempt replaced nothing and left your recovery key as it was. The key shown here opens nothing - discard it, and try again for a new one.');
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
        // this dialog is still on screen. So nothing below asks gone().
        if (remember) window.vaultSession.rememberDevice(remembered);
        else window.vaultSession.forgetDevice();
        this.tellPage('landed', pending);
        window.vaultSession.lock();
      } catch (err) {
        if (!gone()) this.error = gettext('Your recovery key could not be replaced. Try again.');
      } finally {
        this.busy = false;
      }
    },
  };
};
