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

// What a failed attempt at a rotation proves: 'landed' when the envelope in
// place is the one it sealed, 'absent' when it was never applied, and
// otherwise 'unknown' or 'superseded'. Calling either of those a failure
// would have the key's owner throw away what may open the account.
//
// Never applied means that no copy is unanswered: none left, or the server
// itself refused the only one. A copy that got no answer, or a gateway's, may
// be committed for as long as the envelope it names is in place - no read
// rules that out, and no delay either, since nothing bounds how long a
// request is held on its way. That is 'unknown', and only the server's answer
// to the rotation settles it.
//
// With a third envelope in place the rotation can no longer be committed: it
// is conditional on the envelope it read, which never comes back. Whether it
// was, before that envelope arrived, is beyond telling - a password change
// made since with the new key seals a third envelope that this key opens.
// That is 'superseded': over, and nothing may be said of the key.
window.vaultRecoveryKeyOutcome = async function vaultRecoveryKeyOutcome(pending, err, inDoubt) {
  const sent = !!(err && err.rotationSent);
  if (!sent && !inDoubt) return 'absent';
  let envelope;
  try {
    envelope = await window.vaultApi.fetchEnvelope();
  } catch (readError) {
    return 'unknown';
  }
  if (envelope.wrapped_kex_priv === pending.sealed.wrapped_kex_priv) return 'landed';
  const status = err && err.status;
  if (!inDoubt && sent && status >= 400 && status < 500) return 'absent';
  return envelope.wrapped_kex_priv === pending.expected ? 'unknown' : 'superseded';
};

// Said by the dialog while it is open and by the page afterwards: one text,
// so that the two cannot drift. It does not say that a key which unlocks is
// the key in use: the envelope a rotation replaced still opens with the
// credentials before it, and the vault warns when an unlock went through it.
window.vaultRecoveryKeySupersededText = function vaultRecoveryKeySupersededText() {
  return gettext('Your account was changed from another tab or another device, so this page can no longer tell whether the new recovery key it showed you was put in place. Keep that key and its emergency kit with the old ones. The key in use is the one that unlocks your vault without the warning about a previous password or recovery key.');
};

window.vaultRecoveryKeyDialogMixin = function vaultRecoveryKeyDialogMixin() {
  return {
    recoveryKeyDialogOpen: false,
    // '' | 'replaced' | 'discarded' | 'unsettled' | 'superseded' | 'landed'.
    // They survive a lock on purpose: the lock that follows a replacement
    // puts the first on screen, and a lock that took the dialog down is what
    // the next two are for.
    recoveryKeyNotice: '',
    // A new key is on screen, and a kit may have been saved with it, while
    // the account still opens with the old one.
    recoveryKeyShown: false,
    // A rotation that was sent and that nobody can vouch for yet: still out,
    // or unanswered. The two sealed keys and the envelope they replace -
    // ciphertext, so it may wait across a lock for the page to ask what
    // became of it, and to send it again if that is what it takes.
    recoveryKeyUnsettled: null,
    // Whether a copy of that rotation is out right now, the dialog's or this
    // page's own. Its answer will say, and a second copy would only race it.
    recoveryKeyInFlight: false,

    // One rotation in doubt at a time. A second replacement started over it
    // would leave two keys that may each be the one in place, and a page
    // that can only answer for the last; so the first is settled before
    // another dialog opens, and what became of it is said in its place.
    openRecoveryKeyDialog() {
      if (this.recoveryKeyDialogOpen) return;
      if (this.recoveryKeyUnsettled) {
        this.recoveryKeySettle();
        return;
      }
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
    recoveryKeyWarns() {
      return ['discarded', 'unsettled', 'superseded'].includes(this.recoveryKeyNotice);
    },

    recoveryKeyNoticeText() {
      if (this.recoveryKeyNotice === 'superseded') return window.vaultRecoveryKeySupersededText();
      if (this.recoveryKeyNotice === 'discarded') {
        return gettext('Your recovery key was not replaced: nothing changed. The new key you were shown opens nothing - discard it, and any emergency kit saved with it.');
      }
      if (this.recoveryKeyNotice === 'unsettled') {
        return gettext('This page could not check whether your recovery key was replaced. Keep the new key and its emergency kit with the old ones for now: your next unlock on this page will say which is in place.');
      }
      if (this.recoveryKeyNotice === 'landed') {
        return gettext('Your recovery key was replaced after all. Keep the new key and its emergency kit, and unlock with it from now on: that is what retires the old one.');
      }
      return '';
    },

    // What the dialog knows of the rotation it sent: 'sending' from the
    // moment the request leaves, then what vaultRecoveryKeyOutcome made of
    // its answer. Heard on the window, because a lock may have taken the
    // dialog while the request was out and a detached element reaches
    // nothing else.
    recoveryKeyOutcome(outcome, rotation) {
      if (outcome === 'landed') {
        this.recoveryKeyReplaced();
        return;
      }
      if (outcome === 'superseded') {
        this.recoveryKeySuperseded();
        return;
      }
      this.recoveryKeyInFlight = outcome === 'sending';
      this.recoveryKeyUnsettled = outcome === 'absent' ? null : rotation;
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

    // Nothing is in doubt any more, and nothing is known of the key either.
    // Said under an open dialog too, and the key no longer counts as shown:
    // the close that follows must not call void a key that may be in use.
    recoveryKeySuperseded() {
      this.recoveryKeyUnsettled = null;
      this.recoveryKeyInFlight = false;
      this.recoveryKeyShown = false;
      this.recoveryKeyNotice = 'superseded';
    },

    // Once a session is open: the notices have had their say, and the server
    // is in reach again for the one question left open.
    recoveryKeyUnlocked() {
      this.recoveryKeyNotice = '';
      if (this.recoveryKeyUnsettled) this.recoveryKeySettle();
    },

    // Asks what became of the rotation in doubt, and says it. The envelope
    // in place ends the doubt when it has moved: to the rotation's own, which
    // landed, or to a third one, which the rotation can never replace and
    // which says nothing of the key. While it is still the envelope the
    // rotation names, only the server's answer to that rotation settles
    // anything - a request held on its way can be committed at any time, and
    // no wait rules that out. So the rotation is sent again: the one its
    // owner confirmed, conditional on that same envelope, and the old key
    // goes on opening the account until the new one unlocks.
    async recoveryKeySettle() {
      const rotation = this.recoveryKeyUnsettled;
      // Emptied first, and set again only after an await: a notice that was
      // dismissed is built again, so asking twice never looks like nothing.
      this.recoveryKeyNotice = '';
      if (this.recoveryKeyInFlight) {
        await null;
        this.recoveryKeyNotice = 'unsettled';
        return;
      }
      this.recoveryKeyInFlight = true;
      let outcome;
      // Whether this page sent the rotation itself. It then landed under a
      // session the old key had opened, whichever envelope that was.
      let resent = false;
      try {
        // An unlock may be re-wrapping the envelope this rotation names:
        // read before that lands, it would look untouched.
        await window.vaultSession.envelopeWrites();
        const envelope = await window.vaultApi.fetchEnvelope();
        if (envelope.wrapped_kex_priv === rotation.sealed.wrapped_kex_priv) {
          outcome = 'landed';
        } else if (envelope.wrapped_kex_priv !== rotation.expected) {
          outcome = 'superseded';
        } else {
          resent = true;
          try {
            await window.vaultEnvelopeRotation.commit(rotation, function () { return false; });
            outcome = 'landed';
          } catch (err) {
            outcome = await window.vaultRecoveryKeyOutcome(rotation, err, true);
          }
        }
      } catch (err) {
        outcome = 'unknown';
      }
      this.recoveryKeyInFlight = false;
      if (outcome === 'superseded') {
        this.recoveryKeySuperseded();
        return;
      }
      // Anything short of an answer leaves the question as open as it was.
      if (outcome !== 'landed') {
        this.recoveryKeyNotice = 'unsettled';
        return;
      }
      this.recoveryKeyUnsettled = null;
      // A device that holds the old key must not go on offering it: the new
      // one would never be proved and the old one never retired. The dialog
      // that knew the new key is gone, so the device is left remembering
      // none - unless the new key itself opened this session.
      if (resent || window.vaultSession.openedWithPrevious()) {
        window.vaultSession.forgetDevice();
        this.readRememberedSecret();
      }
      this.recoveryKeyNotice = 'landed';
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
    // A rotation is out, or was sent and nobody could say what became of it:
    // the step can no longer tell its reader that nothing has changed.
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

    // On the window, which a detached element still reaches: a lock during
    // the request takes the dialog, not the rotation.
    tellPage(outcome, pending) {
      window.dispatchEvent(new CustomEvent('vault-recovery-key-outcome', {
        detail: { outcome: outcome, rotation: pending },
      }));
    },

    // This rotation is over and the key on screen has no more to wait for:
    // back to the first step, where another attempt draws another one.
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
      // An earlier attempt whose outcome is still open.
      const inDoubt = this.outcomeUnknown;
      this.busy = true;
      this.error = '';
      try {
        // In flight from here. Until the server's word is in, a lock that
        // takes the dialog must not be read as proof that nothing was sent,
        // and the step must not go on saying that nothing has changed.
        this.outcomeUnknown = true;
        this.tellPage('sending', pending);
        try {
          if (!(await window.vaultEnvelopeRotation.commit(pending, gone))) {
            // The dialog went away before this rotation left.
            this.tellPage(inDoubt ? 'unknown' : 'absent', pending);
            return;
          }
        } catch (err) {
          const outcome = await window.vaultRecoveryKeyOutcome(pending, err, inDoubt);
          if (outcome !== 'landed') {
            this.tellPage(outcome, pending);
            if (gone()) return;
            this.outcomeUnknown = outcome === 'unknown';
            if (outcome === 'unknown') {
              this.error = gettext('This page could not check whether your recovery key was replaced. Keep this key and try again.');
            } else if (outcome === 'superseded') {
              this.withdraw();
              this.error = window.vaultRecoveryKeySupersededText();
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
