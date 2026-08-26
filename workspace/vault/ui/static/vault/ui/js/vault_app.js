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
  var MESSAGES = {
    password: 'That master password does not open this account. Nothing was sent to the server - the check happens here.',
    identity: 'This account has no vault identity yet.',
    'substituted-key': 'The signing key the server returned does not match the one your password unwrapped. Nothing was decrypted. Do not enter your password again on this page.',
    network: 'The vault could not be reached. Check your connection and try again.',
    'recovery-key': 'Your recovery key could not be read. Dashes and case do not matter - check it against your emergency kit.',
  };

  function zero(buffer) {
    if (buffer && buffer.fill) buffer.fill(0);
  }

  // The metadata key is opened fresh per vault and lives only for the one
  // decryption below - nothing keeps it past this call.
  async function decryptVault(row) {
    var V = window.VaultCrypto;
    var payload = V.vaultMetadataPayload(
      Object.assign({}, row, { vault_uuid: row.uuid })
    );
    try {
      await window.VaultSession.verifyVaultMetadata(payload, row.metadata_sig);
    } catch (err) {
      // Signed by nobody the account trusts: never shown with a name that
      // came along for the ride.
      return Object.assign({}, row, { tampered: true, name: '' });
    }
    if (!row.wrapped_key) {
      return Object.assign({}, row, { unopenable: true, name: '' });
    }
    var metaKey;
    try {
      metaKey = await window.VaultSession.openVaultKey(row.uuid, row.wrapped_key);
      var plaintext = await V.open(
        metaKey,
        V.fromBase64Url(row.encrypted_name),
        V.AD.vaultFieldAd(row.uuid, 'name')
      );
      return Object.assign({}, row, { name: new TextDecoder().decode(plaintext) });
    } catch (err) {
      // Localised the same way a bad signature is: one row loses its name,
      // the rest of the list - and the Promise.all it resolves inside -
      // keeps going.
      return Object.assign({}, row, { unreadable: true, name: '' });
    } finally {
      zero(metaKey);
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
      vaults: [],
      busy: false,
      showCreate: false,
      newVaultName: '',
      // Alpine's reactivity only sees property reads, never a call into
      // VaultSession's own closure - so the countdown template binds this
      // property, and onTick is what keeps it current.
      secondsLeft: 0,

      init: function () {
        var remembered = window.VaultSession.rememberedSecret();
        if (remembered) {
          this.secretText = remembered;
          this.remember = true;
        }
        this.secretRequired = !remembered;
        var self = this;
        window.VaultSession.onLock(function () {
          self.vaults = [];
          self.state = 'locked';
          self.newVaultName = '';
        });
        window.VaultSession.onTick(function () {
          self.secondsLeft = window.VaultSession.secondsUntilLock();
        });
        window.VaultSession.watchForIdle();
      },

      secretMissing: function () {
        return this.secretRequired && !this.secretText;
      },

      unlock: async function () {
        this.state = 'deriving';
        this.error = '';
        try {
          await window.VaultSession.unlock({
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
            window.VaultSession.forgetDevice();
          }
          // A wrong password must not survive into the retry.
          this.password = '';
          return;
        }
        this.password = '';
        this.state = 'unlocked';
        this.secondsLeft = window.VaultSession.secondsUntilLock();
        try {
          await this.loadVaults();
        } catch (err) {
          // The session opened; only the listing failed. Falling back to
          // 'locked' here would show the password form while VaultSession
          // still holds live keys with an unreachable countdown and Lock
          // now button - the exact disagreement this branch exists to
          // avoid, so the state stays 'unlocked' and the failure is
          // reported in place instead.
          this.error = 'The vault list could not be loaded. Try again.';
        }
      },

      loadVaults: async function () {
        var rows = await window.VaultApi.listVaults();
        this.vaults = await Promise.all(rows.map(decryptVault));
      },

      createVault: async function () {
        if (!window.VaultSession.isUnlocked()) return;
        var name = this.newVaultName.trim();
        if (!name) return;
        this.busy = true;
        this.error = '';
        try {
          var body = await window.buildVaultCreateRequest(window.VaultSession, name);
          var row = await window.VaultApi.createVault(body);
          this.vaults.push(await decryptVault(row));
          this.showCreate = false;
          this.newVaultName = '';
        } catch (err) {
          this.error = err.status === 409
            ? 'A vault with that name could not be created - its identifier collided. Try again.'
            : 'The vault could not be created. Try again.';
        } finally {
          this.busy = false;
        }
      },

      lockNow: function () {
        window.VaultSession.lock();
      },

      countdown: function () {
        var minutes = Math.floor(this.secondsLeft / 60);
        var rest = this.secondsLeft % 60;
        return minutes + ':' + String(rest).padStart(2, '0');
      },
    };
  };
})();
