// The unlock screen: password + recovery key in, an open vault list out.
// The 'deriving' state exists because Argon2id at 64 MiB takes several
// hundred milliseconds by design - an unexplained wait on a login form reads
// as broken, so the screen says what is happening instead of just spinning.

window.vaultApp = (function () {
  // The message a wrong password gets must say the check happened locally:
  // no request carries the password, so there is nothing for the user to
  // worry leaked. The message a substituted key gets must NOT read like a
  // retryable error - it is the detection the whole scheme exists for, and
  // inviting a retry would mean typing a password into a page that may be
  // hostile.
  const MESSAGES = {
    password: 'That master password does not open this account. Nothing was sent to the server - the check happens here.',
    identity: 'This account has no vault identity yet.',
    'substituted-key': 'The signing key the server returned does not match the one your password unwrapped. Nothing was decrypted. Do not enter your password again on this page.',
    network: 'The vault could not be reached. Check your connection and try again.',
    throttled: 'Too many unlock attempts from here. Nothing is wrong with your password - wait a minute and try again.',
    'recovery-key': 'Your recovery key could not be read. Dashes and case do not matter - check it against your emergency kit.',
    'password-or-recovery-key': 'That did not open this account. Either the master password is wrong, or the recovery key remembered on this device belongs to another account - both fail the same way. Nothing was sent to the server.',
  };

  // The metadata key is opened fresh per vault and is non-extractable: no
  // copy of its bytes exists on this side to be zeroed.
  async function decryptVault(row) {
    const V = window.vaultCrypto;
    const payload = V.vaultMetadataPayload(
      Object.assign({}, row, { vault_uuid: row.uuid })
    );
    try {
      await window.vaultSession.verifyVaultMetadata(payload, row.metadata_sig);
    } catch (err) {
      // A lock landing mid-listing fails this the same way a forged signature
      // does, and the tamper alert is the one message the user is told to act
      // on rather than retry - so it must never stand in for an idle timeout.
      if (err && err.reason === 'locked') throw err;
      // Signed by nobody the account trusts: never shown with a name that
      // came along for the ride.
      return Object.assign({}, row, { tampered: true, name: '' });
    }
    if (!row.wrapped_key) {
      return Object.assign({}, row, { unopenable: true, name: '' });
    }
    try {
      const metaKey = await window.vaultSession.openVaultKey(row.uuid, row.wrapped_key);
      const plaintext = await V.open(
        metaKey,
        V.fromBase64Url(row.encrypted_name),
        V.AD.vaultFieldAd(row.uuid, 'name')
      );
      return Object.assign({}, row, { name: new TextDecoder().decode(plaintext) });
    } catch (err) {
      if (err && err.reason === 'locked') throw err;
      // Localised the same way a bad signature is: one row loses its name,
      // the rest of the list - and the Promise.all it resolves inside -
      // keeps going.
      return Object.assign({}, row, { unreadable: true, name: '' });
    }
  }

  return function vaultApp() {
    return {
      state: 'locked',
      error: '',
      password: '',
      secretText: '',
      remember: false,
      // Whether this device has to be asked for a recovery key, decided once
      // at mount. It must not follow secretText: x-model rewrites that on
      // every keystroke, and a gate reading it would unmount the field on its
      // own first character - the key could then never be typed, only pasted.
      secretRequired: false,
      // Whether the key in secretText came from this device rather than from
      // the user's hands. It is what lets a failed unlock tell "you mistyped
      // the password" apart from "the key we remembered is not yours".
      secretRemembered: false,
      vaults: [],
      busy: false,
      showCreate: false,
      newVaultName: '',
      // Minted on the first attempt and kept until one succeeds: it is the
      // server's idempotency key, so every retry of the same vault has to
      // reuse it.
      pendingVaultUuid: null,
      // Alpine's reactivity only sees property reads, never a call into
      // vaultSession's own closure - so the countdown template binds this
      // property, and onTick is what keeps it current.
      secondsLeft: 0,

      init: function () {
        const remembered = window.vaultSession.rememberedSecret();
        if (remembered) {
          this.secretText = remembered;
          this.remember = true;
        }
        this.secretRequired = !remembered;
        this.secretRemembered = !!remembered;
        const self = this;
        window.vaultSession.onLock(function () {
          self.vaults = [];
          self.state = 'locked';
          // showCreate has to go with the name: the dialog lives inside the
          // unlocked subtree, so a lock hides it without closing it, and the
          // next unlock reopens it on its own.
          self.closeCreateDialog();
        });
        window.vaultSession.onTick(function () {
          self.secondsLeft = window.vaultSession.secondsUntilLock();
        });
        window.vaultSession.watchForIdle();
      },

      secretMissing: function () {
        return this.secretRequired && !this.secretText;
      },

      unlock: async function () {
        this.state = 'deriving';
        this.error = '';
        try {
          await window.vaultSession.unlock({
            password: this.password,
            secretText: this.secretText,
            remember: this.remember,
          });
        } catch (err) {
          this.state = 'locked';
          this.error = MESSAGES[err.reason] || 'Something went wrong. Try again.';
          // A key that fails to decode - remembered or freshly typed - must
          // not leave the user with no way back: clearing the value and
          // re-arming the gate is what puts the input back on screen, and
          // forgetting the device stops a bad remembered value from repeating
          // this on every load.
          if (err.reason === 'recovery-key') {
            this.secretText = '';
            this.secretRequired = true;
            this.secretRemembered = false;
            window.vaultSession.forgetDevice();
          }
          // A recovery key belonging to another account decodes cleanly and
          // then fails the very tag a mistyped password fails, so this branch
          // cannot tell the two apart and must not pick one. Putting the
          // remembered key back on screen - filled in, ready to be replaced -
          // is the only thing that makes the second case correctable; without
          // it every attempt fails, blames the password, and hides the field
          // that is actually wrong.
          if (err.reason === 'password' && this.secretRemembered) {
            this.secretRequired = true;
            this.error = MESSAGES['password-or-recovery-key'];
          }
          // A wrong password must not survive into the retry.
          this.password = '';
          return;
        }
        this.password = '';
        this.state = 'unlocked';
        this.secondsLeft = window.vaultSession.secondsUntilLock();
        try {
          await this.loadVaults();
        } catch (err) {
          // The session opened; only the listing failed. Falling back to
          // 'locked' here would show the password form while vaultSession
          // still holds live keys with an unreachable countdown and Lock
          // now button - the exact disagreement this branch exists to
          // avoid, so the state stays 'unlocked' and the failure is
          // reported in place instead.
          this.error = 'The vault list could not be loaded. Try again.';
        }
      },

      loadVaults: async function () {
        const rows = await window.vaultApi.listVaults();
        let decrypted;
        try {
          decrypted = await Promise.all(rows.map(decryptVault));
        } catch (err) {
          // A lock caught the rows mid-flight. There is nothing to report:
          // the user is looking at the password form, and the caller's
          // message would blame the listing for an idle timeout.
          if (err && err.reason === 'locked') return;
          throw err;
        }
        // Neither await is atomic with the lock: an idle timeout or a hidden
        // tab can fire in between, and by then the onLock callback has already
        // emptied the list. Assigning anyway would put decrypted names back
        // into a locked component - reachable state a lock exists to clear,
        // even though nothing renders it.
        if (!window.vaultSession.isUnlocked()) return;
        this.vaults = decrypted;
      },

      createVault: async function () {
        if (!window.vaultSession.isUnlocked()) return;
        const name = this.newVaultName.trim();
        if (!name) return;
        this.busy = true;
        this.error = '';
        if (!this.pendingVaultUuid) {
          this.pendingVaultUuid = window.vaultCrypto.uuidV7();
        }
        try {
          const body = await window.buildVaultCreateRequest(
            window.vaultSession, name, this.pendingVaultUuid
          );
          const row = await window.vaultApi.createVault(body);
          const created = await decryptVault(row);
          // Same race loadVaults guards against: three awaits sit between the
          // check at the top of this function and here, so a lock can have
          // emptied the list already. pendingVaultUuid survives on purpose -
          // the vault was written, and a retry after re-unlocking must reuse
          // it.
          if (!window.vaultSession.isUnlocked()) return;
          this.vaults.push(created);
          this.closeCreateDialog();
        } catch (err) {
          // The vault is written; only the local half was cut short. Saying
          // it could not be created would be false, and the retry after the
          // next unlock reuses pendingVaultUuid to find it.
          if (err && err.reason === 'locked') return;
          if (err.status === 409) {
            // The 409 says the UUID is taken, not that it is taken by us: it
            // comes from a globally unique primary key, so a row on another
            // account answers the same. Reading the reload back is what turns
            // the assumption that this is the vault a lost answer already
            // wrote into something checked.
            try {
              await this.loadVaults();
              if (!window.vaultSession.isUnlocked()) return;
              const pending = this.pendingVaultUuid;
              if (!this.vaults.some(function (v) { return v.uuid === pending; })) {
                this.error = 'The vault could not be created. Try again.';
                return;
              }
              this.closeCreateDialog();
            } catch (reloadErr) {
              this.error = 'Your vault was created, but the list could not be reloaded.';
            }
          } else {
            this.error = 'The vault could not be created. Try again.';
          }
        } finally {
          this.busy = false;
        }
      },

      closeCreateDialog: function () {
        this.showCreate = false;
        this.newVaultName = '';
        this.pendingVaultUuid = null;
      },

      lockNow: function () {
        window.vaultSession.lock();
      },

      countdown: function () {
        const minutes = Math.floor(this.secondsLeft / 60);
        const rest = this.secondsLeft % 60;
        return minutes + ':' + String(rest).padStart(2, '0');
      },
    };
  };
})();
