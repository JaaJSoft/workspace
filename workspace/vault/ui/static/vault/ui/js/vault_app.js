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
    password: 'That vault password does not open this account. Nothing was sent to the server - the check happens here.',
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
    var metaKey = await window.VaultSession.openVaultKey(row.uuid, row.wrapped_key);
    try {
      var plaintext = await V.open(
        metaKey,
        V.fromBase64Url(row.encrypted_name),
        V.AD.vaultFieldAd(row.uuid, 'name')
      );
      return Object.assign({}, row, { name: new TextDecoder().decode(plaintext) });
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
      vaults: [],
      busy: false,
      showCreate: false,
      newVaultName: '',

      init: function () {
        var remembered = window.VaultSession.rememberedSecret();
        if (remembered) {
          this.secretText = remembered;
          this.remember = true;
        }
        var self = this;
        window.VaultSession.onLock(function () {
          self.vaults = [];
          self.state = 'locked';
          self.newVaultName = '';
        });
        window.VaultSession.watchForIdle();
      },

      needsSecret: function () {
        return !this.secretText;
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
          this.state = 'unlocked';
          await this.loadVaults();
        } catch (err) {
          this.state = 'locked';
          this.error = MESSAGES[err.reason] || 'Something went wrong. Try again.';
        } finally {
          // A wrong password must not survive into the retry.
          this.password = '';
        }
      },

      loadVaults: async function () {
        var rows = await window.VaultApi.listVaults();
        this.vaults = await Promise.all(rows.map(decryptVault));
      },

      lockNow: function () {
        window.VaultSession.lock();
      },

      countdown: function () {
        var seconds = window.VaultSession.secondsUntilLock();
        var minutes = Math.floor(seconds / 60);
        var rest = seconds % 60;
        return minutes + ':' + String(rest).padStart(2, '0');
      },
    };
  };
})();
