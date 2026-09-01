// The unlock gate: the whole of what the vault shows until the account is
// open, since a locked page has no vault to navigate and no filter to apply.
//
// A mixin rather than part of the controller: the wording below distinguishes
// a mistyped password from a substituted signing key, and that distinction is
// worth having one place to go wrong rather than two.
//
// A component spreading this mixin defines two hooks:
//   afterUnlock()  what to load once the keys are live, errors included
//   onLocked()     what to drop when the keys leave memory
//
// The 'deriving' state exists because Argon2id at 64 MiB takes several
// hundred milliseconds by design - an unexplained wait on a login form reads
// as broken, so the screen says what is happening instead of just spinning.
window.vaultUnlockMixin = (function () {
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

  return function vaultUnlockMixin() {
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
      // Alpine's reactivity only sees property reads, never a call into
      // vaultSession's own closure - so the countdown template binds this
      // property, and onTick is what keeps it current.
      secondsLeft: 0,

      initUnlock: function () {
        const remembered = window.vaultSession.rememberedSecret();
        if (remembered) {
          this.secretText = remembered;
          this.remember = true;
        }
        this.secretRequired = !remembered;
        this.secretRemembered = !!remembered;
        const self = this;
        window.vaultSession.onLock(function () {
          self.state = 'locked';
          self.onLocked();
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
        // The session opened; whatever the screen loads next is its own
        // business, failures included. Falling back to 'locked' out here
        // would show the password form while vaultSession still holds live
        // keys with an unreachable countdown and Lock now button.
        await this.afterUnlock();
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
