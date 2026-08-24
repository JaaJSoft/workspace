// Vault onboarding: three steps, the last one impossible to skip.
//
// Everything that matters happens in this file. The server receives an opaque
// envelope and never the vault password, the recovery secret, or anything
// derived from them - see test_secret_never_posted.py, which fails the build
// if a future edit lets the secret near a request body.

// The floor the norm sets: twelve code points after NFC, a zxcvbn score of at
// least three, and absence from the breach corpus. No composition rules.
var VAULT_MIN_PASSWORD_LENGTH = 12;
var VAULT_MIN_PASSWORD_SCORE = 3;

window.vaultOnboarding = function vaultOnboarding() {
  return {
    step: 1,
    password: '',
    confirmation: '',
    score: null,
    feedback: '',
    // unchecked | checking | clean | found | unavailable
    breachStatus: 'unchecked',
    secretText: '',
    secretBytes: null,
    accountUuid: '',
    acknowledged: false,
    busy: false,
    error: '',
    // One token per keystroke, read by both lookups. The corpus answer takes
    // as long as the network wants: without it, a reply about a password the
    // user has already replaced can overwrite the verdict on the one in the
    // field, and a stale "clean" lets a breached password through the floor.
    generation: 0,

    // Code points after NFC, not UTF-16 units: someone who typed twelve
    // characters typed twelve characters, whatever the encoding costs.
    passwordLongEnough() {
      return (
        Array.from(this.password.normalize('NFC')).length >=
        VAULT_MIN_PASSWORD_LENGTH
      );
    },

    passwordStrongEnough() {
      return this.score !== null && this.score >= VAULT_MIN_PASSWORD_SCORE;
    },

    // A lookup that could not run is not a password that was found: an
    // unreachable third party must never stop someone protecting their vault.
    // A lookup that has not run yet is a different matter - treating it as a
    // pass would drop one of the floor's three criteria for anyone who never
    // blurs the field.
    passwordChecked() {
      return this.breachStatus === 'clean' || this.breachStatus === 'unavailable';
    },

    passwordBlocked() {
      return this.breachStatus === 'found';
    },

    passwordsMatch() {
      return this.password.length > 0 && this.password === this.confirmation;
    },

    passwordAcceptable() {
      return (
        this.passwordLongEnough() &&
        this.passwordStrongEnough() &&
        this.passwordsMatch() &&
        this.passwordChecked() &&
        !this.passwordBlocked()
      );
    },

    canFinish() {
      return this.acknowledged;
    },

    passwordChanged() {
      this.generation++;
      this.evaluateStrength();
      this.checkBreachCorpus();
    },

    async evaluateStrength() {
      var generation = this.generation;
      if (!this.password) {
        this.score = null;
        this.feedback = '';
        return;
      }
      var result = await window.VaultOnboarding.estimateStrength(this.password);
      if (generation !== this.generation) return;
      this.score = result.score;
      this.feedback = (result.feedback && result.feedback.warning) || '';
    },

    // k-anonymity: only the first five hex characters of the SHA-1 leave the
    // device, and the answer is a list of suffixes we match locally. The
    // password itself never crosses the network.
    async checkBreachCorpus() {
      var generation = this.generation;
      if (!this.password) {
        this.breachStatus = 'unchecked';
        return;
      }
      this.breachStatus = 'checking';
      try {
        // SHA-1 through WebCrypto rather than through the bundle: the corpus
        // is indexed by it, and adding a hash nobody else needs would spend
        // the main bundle's byte budget on one caller.
        var bytes = new TextEncoder().encode(this.password);
        var digest = Array.from(
          new Uint8Array(await crypto.subtle.digest('SHA-1', bytes))
        )
          .map(function (byte) {
            return byte.toString(16).padStart(2, '0');
          })
          .join('')
          .toUpperCase();
        var prefix = digest.slice(0, 5);
        var suffix = digest.slice(5);
        var response = await fetch(
          'https://api.pwnedpasswords.com/range/' + prefix
        );
        if (!response.ok) throw new Error('breach lookup failed');
        var body = await response.text();
        var found = body.split('\n').some(function (line) {
          return line.split(':')[0].trim().toUpperCase() === suffix;
        });
        if (generation !== this.generation) return;
        this.breachStatus = found ? 'found' : 'clean';
      } catch (err) {
        if (generation !== this.generation) return;
        this.breachStatus = 'unavailable';
      }
    },

    // The norm wipes the vault password and the recovery secret as soon as the
    // kit has been shown. The text on screen has to survive - the user is
    // copying it - but nothing else does.
    forgetSecrets() {
      this.password = '';
      this.confirmation = '';
      if (this.secretBytes) this.secretBytes.fill(0);
    },

    groupedSecret() {
      return (this.secretText.match(/.{1,4}/g) || []).join('-');
    },

    // The whole sealing flow, in the order the norm sets out: init for the
    // salt and the account identifier, derive, wrap, attest, finalize.
    async generateAndSeal() {
      var V = window.VaultCrypto;
      this.busy = true;
      this.error = '';
      // Whether the finalize request left the browser. What follows a failure
      // depends entirely on it.
      var sent = false;
      try {
        var started = await this.post('/api/v1/vault/account/init');
        if (!started.ok) {
          throw new Error('the server refused to start an account');
        }
        var account = await started.json();
        this.accountUuid = account.account_uuid;

        // Minted once, kept across retries. init is idempotent while the
        // identity is pending, so a second attempt derives the same keys - but
        // only from the same secret. Drawing a fresh one would put a secret on
        // screen that does not open the vault the first attempt may have
        // already sealed.
        if (!this.secretBytes) {
          this.secretBytes = V.randomBytes(32);
          this.secretText = V.crockfordEncode(this.secretBytes);
        }

        var amk = await V.deriveAmk({
          password: this.password.normalize('NFC'),
          secretKey: this.secretBytes,
          salt: V.fromBase64Url(account.kdf_salt),
        });
        var unwrapKey = await V.hkdf(amk, V.AD.unwrapInfo());

        var kexPair = await crypto.subtle.generateKey('X25519', true, [
          'deriveBits',
        ]);
        var sigPair = await crypto.subtle.generateKey('Ed25519', true, [
          'sign',
          'verify',
        ]);
        var kexPrivate = new Uint8Array(
          await crypto.subtle.exportKey('pkcs8', kexPair.privateKey)
        ).slice(-32);
        // WebCrypto exports an Ed25519 private key as PKCS#8 only, and the
        // bundle signs from the bare seed: the last 32 bytes of that fixed
        // structure.
        var sigSeed = new Uint8Array(
          await crypto.subtle.exportKey('pkcs8', sigPair.privateKey)
        ).slice(-32);

        var kexPublic = V.toBase64Url(
          V.encodePublicKey(
            new Uint8Array(await crypto.subtle.exportKey('raw', kexPair.publicKey)),
            V.PUBKEY_ALG_X25519
          )
        );
        var sigPublic = V.toBase64Url(
          V.encodePublicKey(
            new Uint8Array(await crypto.subtle.exportKey('raw', sigPair.publicKey)),
            V.PUBKEY_ALG_ED25519
          )
        );

        var sealed = {
          keyVersion: 1,
          kdfId: V.KDF_HKDF_SHA256,
        };
        var body = {
          kdf_algo: 'argon2id',
          kdf_params: V.ARGON2_PARAMS,
          kex_public: kexPublic,
          sig_public: sigPublic,
          wrapped_kex_priv: V.toBase64Url(
            await V.seal(
              unwrapKey,
              kexPrivate,
              V.AD.kexPrivAd(this.accountUuid),
              sealed
            )
          ),
          wrapped_sig_priv: V.toBase64Url(
            await V.seal(
              unwrapKey,
              sigSeed,
              V.AD.sigPrivAd(this.accountUuid),
              sealed
            )
          ),
          sig_over_kex_pub: V.toBase64Url(
            await V.signBytes(
              sigSeed,
              V.AD.kexPubPayload(this.accountUuid, kexPublic)
            )
          ),
        };

        sent = true;
        var finalized = await this.post(
          '/api/v1/vault/account/finalize',
          body
        );
        if (finalized.status !== 201) {
          throw new Error('the server refused the account envelope');
        }
        this.step = 3;
        this.forgetSecrets();
      } catch (err) {
        // A lost response is not a lost account. finalize may have committed
        // before the connection died, and the identity it activated can only
        // ever be opened with the secret this page is still holding: claiming
        // nothing was saved would send the user away from the one screen that
        // can still show it. Ask the server which of the two happened.
        if (sent && (await this.accountIsActive())) {
          this.step = 3;
          this.forgetSecrets();
          return;
        }
        this.error =
          'Your vault could not be created. Nothing was saved; try again.';
      } finally {
        this.busy = false;
      }
    },

    // Pessimistic on purpose: a lookup that cannot answer reads as "not
    // active", which is the reply that tells the user to try again.
    async accountIsActive() {
      try {
        var response = await fetch('/api/v1/vault/account/envelope', {
          headers: { Accept: 'application/json' },
        });
        if (!response.ok) return false;
        var envelope = await response.json();
        return envelope.state === 'active';
      } catch (err) {
        return false;
      }
    },

    downloadKit() {
      var blob = window.VaultOnboarding.buildEmergencyKitPdf({
        email: this.$root.dataset.email,
        serverUrl: window.location.origin,
        secretText: this.groupedSecret(),
        createdAt: new Date().toISOString().slice(0, 10),
      });
      var url = URL.createObjectURL(blob);
      var link = document.createElement('a');
      link.href = url;
      link.download = 'vault-emergency-kit.pdf';
      // In the document, not detached: Firefox ignores a click on an anchor
      // that was never inserted, and the user is left with no kit and no
      // error - on the one screen where the secret cannot be recovered later.
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    },

    post(url, body) {
      return fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCSRFToken(),
        },
        body: JSON.stringify(body || {}),
      });
    },
  };
};
