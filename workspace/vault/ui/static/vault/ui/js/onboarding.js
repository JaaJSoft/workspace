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
    sentKexPublic: '',
    vaultSigner: null,
    accountKexPublic: null,
    acknowledged: false,
    remember: false,
    leaveGuard: null,
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

    // x-model writes the field through on every keystroke while the lookups
    // wait out the debounce. Without this the floor keeps reporting the
    // previous password's verdict for those 400 ms, and a password manager
    // filling both fields at once clears it on a value nobody evaluated.
    passwordEdited() {
      this.generation++;
      this.score = null;
      this.feedback = '';
      this.breachStatus = 'unchecked';
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
      try {
        var result = await window.VaultOnboarding.estimateStrength(this.password);
        if (generation !== this.generation) return;
        this.score = result.score;
        this.feedback = (result.feedback && result.feedback.warning) || '';
      } catch (err) {
        // The floor stays closed - an unmeasured password is not a strong one
        // - but silence would leave a button that refuses to enable and no
        // reason on screen.
        if (generation !== this.generation) return;
        this.score = null;
        this.feedback = 'could not be checked on this device';
      }
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

    // The recovery key lives in this page and nowhere else - not on the
    // server, not in storage - until the box is ticked. The browser's own
    // prompt is the only thing that reaches a reload or a closed tab, which
    // is more than any amount of markup can do.
    guardAgainstLeaving() {
      if (this.leaveGuard) return;
      this.leaveGuard = function (event) {
        if (!this.secretText || this.acknowledged) return;
        event.preventDefault();
        // Chrome still wants the legacy property set; the string it carries
        // is ignored, every browser shows its own wording.
        event.returnValue = '';
      }.bind(this);
      window.addEventListener('beforeunload', this.leaveGuard);
    },

    groupedSecret() {
      return (this.secretText.match(/.{1,4}/g) || []).join('-');
    },

    // Written from the kit screen, in the grouped spelling the sheet shows:
    // crockfordDecode ignores the hyphens and the case, so what is stored is
    // exactly what the user could retype.
    //
    // Failing to remember the device does not mean failing to finish
    // onboarding - by the time this runs, finish() has either already
    // created the vault or has nowhere left to send the user but the vault
    // screen, and a storage throw here must never turn either outcome into
    // a reported failure or block the navigation that follows it.
    rememberOnThisDevice() {
      try {
        if (this.remember) {
          localStorage.setItem('vault.secret-key', this.groupedSecret());
        } else {
          localStorage.removeItem('vault.secret-key');
        }
      } catch (err) {
        // Best-effort only; nothing to recover, nothing to zero.
      }
    },

    // The last step of onboarding and the first vault are one action from the
    // user's side. Splitting them would leave an account that is set up and
    // has nowhere to put anything.
    async finish() {
      if (!this.canFinish() || this.busy) return;
      this.busy = true;
      this.error = '';
      try {
        // A user can reach this step through the lost-response recovery path
        // in generateAndSeal, which only rebuilds the signer when the same
        // attempt drew the keys - a retry that found the account active
        // without generating anything holds none. The identity is active
        // either way: send them to the vault screen, which can still create
        // one, rather than parking them behind a retry that can never
        // succeed because there is nothing left here to sign with.
        if (!this.vaultSigner || !this.accountKexPublic) {
          this.rememberOnThisDevice();
          window.location.assign(this.$root.dataset.vaultUrl);
          return;
        }
        var body = await window.buildVaultCreateRequest(this.vaultSession(), 'Personal');
        await window.VaultApi.createVault(body);
        this.rememberOnThisDevice();
        window.location.assign(this.$root.dataset.vaultUrl);
      } catch (err) {
        // The identity is active and the kit has been shown: the account is
        // sound, only the vault is missing. Saying so keeps the retry on the
        // one screen that can still explain it.
        this.error =
          'Your vault could not be created. Your account is set up and your ' +
          'recovery key is valid - try again.';
      } finally {
        this.busy = false;
      }
    },

    vaultSession() {
      var self = this;
      return {
        accountUuid: function () { return self.accountUuid; },
        accountKexPublicRaw: function () { return self.accountKexPublic; },
        sign: async function (payload) {
          return window.VaultCrypto.toBase64Url(
            await self.vaultSigner.sign(window.VaultCrypto.canonicalCbor(payload))
          );
        },
      };
    },

    // Shared by the direct success path below and its own lost-response
    // recovery branch: both confirm the same envelope landed, and both need
    // a signer and the account's own key-exchange public key for the vault
    // this step is about to create.
    async captureVaultSigningMaterial(sigSeed, kexPrivate, kexPublic) {
      var V = window.VaultCrypto;
      this.vaultSigner = await V.importSigner(sigSeed);
      this.accountKexPublic = V.decodePublicKey(V.fromBase64Url(kexPublic));
      sigSeed.fill(0);
      kexPrivate.fill(0);
    },

    // The whole sealing flow, in the order the norm sets out: init for the
    // salt and the account identifier, derive, wrap, attest, finalize.
    async generateAndSeal() {
      var V = window.VaultCrypto;
      this.busy = true;
      this.error = '';
      // init answers 409 only when the identity is already active. On a first
      // attempt that means somewhere else sealed it; on a retry it means our
      // own finalize landed after all, and the reply was what went missing.
      var conflict = false;
      try {
        var started = await this.post('/api/v1/vault/account/init');
        if (!started.ok) {
          conflict = started.status === 409;
          throw new Error('the server refused to start an account');
        }
        var account = await started.json();
        this.accountUuid = account.account_uuid;

        this.guardAgainstLeaving();
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

        // Kept beyond this attempt: it is how a later failure can tell our
        // own envelope from one another tab sealed.
        this.sentKexPublic = kexPublic;
        var finalized = await this.post(
          '/api/v1/vault/account/finalize',
          body
        );
        if (finalized.status !== 201) {
          throw new Error('the server refused the account envelope');
        }
        // The first vault is sealed on this same page, right after the kit -
        // it needs a signer and the account's own key-exchange public key,
        // neither of which forgetSecrets() below may keep.
        await this.captureVaultSigningMaterial(sigSeed, kexPrivate, kexPublic);
        this.step = 3;
        this.forgetSecrets();
      } catch (err) {
        // Whether the identity is active is the wrong question: another tab
        // may have sealed it with a secret this page never saw. What decides
        // is whether the key on the server is the one we sent.
        var landed = await this.sealedByThisPage();
        if (landed === 'ours') {
          // sigSeed only exists when this very attempt is the one that drew
          // the keys - a retry that got a 409 before generating anything
          // holds none, even though an earlier attempt's envelope is the one
          // now confirmed active. finish() falls back to the vault screen
          // itself when it finds no signer here.
          if (sigSeed) {
            await this.captureVaultSigningMaterial(sigSeed, kexPrivate, kexPublic);
          }
          this.step = 3;
          this.forgetSecrets();
          return;
        }
        if (landed === 'elsewhere') {
          this.error =
            'Your vault was already set up elsewhere, with a different ' +
            'recovery key. The key on this page does not open it.';
        } else if (conflict && !this.sentKexPublic) {
          this.error =
            'Your vault has already been set up. Reload this page to open it.';
        } else if (conflict) {
          // 409 means an active identity exists, and sentKexPublic means this
          // page sent an envelope - so the probe above is the only thing that
          // could have said whose. It could not answer, which leaves exactly
          // one certainty: something was saved. Telling the user otherwise
          // walks them off the only screen that can still show the key.
          this.error =
            'Your vault is already set up, but we could not confirm from ' +
            'here whether this page holds its recovery key. Do not close ' +
            'this page - try again.';
        } else {
          this.error =
            'Your vault could not be created. Nothing was saved; try again.';
        }
      } finally {
        this.busy = false;
      }
    },

    // Three answers, not two - and anything it cannot establish reads as
    // 'no': showing a recovery key that opens nothing is worse than asking
    // for a retry that costs a click.
    async sealedByThisPage() {
      if (!this.sentKexPublic) return 'no';
      try {
        var response = await fetch('/api/v1/vault/account/envelope', {
          headers: { Accept: 'application/json' },
        });
        if (!response.ok) return 'no';
        var envelope = await response.json();
        if (envelope.state !== 'active') return 'no';
        return envelope.kex_public === this.sentKexPublic ? 'ours' : 'elsewhere';
      } catch (err) {
        return 'no';
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
      // Revoked on the next task, not this one: the browser may not have
      // started reading the blob when click() returns, and a revoked URL
      // cancels the download as quietly as a detached anchor does.
      setTimeout(function() {
        URL.revokeObjectURL(url);
      }, 0);
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
