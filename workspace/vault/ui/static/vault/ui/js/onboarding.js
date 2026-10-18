// Vault onboarding: three steps, the last one impossible to skip.
//
// Everything that matters happens in this file. The server receives an opaque
// envelope and never the vault password, the recovery secret, or anything
// derived from them - see test_secret_never_posted.py, which fails the build
// if a future edit lets the secret near a request body.

window.vaultOnboarding = function vaultOnboarding() {
  return {
    ...window.vaultPasswordFloorMixin(),
    // False only on a plain-HTTP origin other than localhost, where the
    // browser withholds crypto.subtle and no step past the first can work.
    secureContext: window.isSecureContext !== false,
    step: 1,
    secretText: '',
    secretBytes: null,
    accountUuid: '',
    sentKexPublic: '',
    vaultSigner: null,
    accountKexPublic: null,
    // The first vault's idempotency key, minted on the first attempt and kept
    // across retries: the server turns a lost answer into a conflict by
    // matching it, and a fresh one would describe a second vault instead.
    firstVaultUuid: null,
    acknowledged: false,
    remember: false,
    leaveGuard: null,
    busy: false,
    error: '',

    canFinish() {
      return this.acknowledged;
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
          localStorage.setItem(VAULT_SECRET_STORAGE_KEY, this.groupedSecret());
        } else {
          localStorage.removeItem(VAULT_SECRET_STORAGE_KEY);
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
        // Minted here rather than inside the builder: the builder runs once
        // per attempt, and a UUID minted there would be a new one each time.
        if (!this.firstVaultUuid) {
          this.firstVaultUuid = window.vaultCrypto.uuidV7();
        }
        const body = await window.buildVaultCreateRequest(
          this.vaultSession(), { name: 'Personal' }, this.firstVaultUuid
        );
        await window.vaultApi.createVault(body);
        this.rememberOnThisDevice();
        window.location.assign(this.$root.dataset.vaultUrl);
      } catch (err) {
        // A conflict on a UUID this page minted is the vault the previous
        // attempt wrote and never heard back about - the outcome the user
        // asked for, reached the slow way.
        if (err && err.status === 409) {
          this.rememberOnThisDevice();
          window.location.assign(this.$root.dataset.vaultUrl);
          return;
        }
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
      const self = this;
      return {
        accountUuid: function () { return self.accountUuid; },
        accountKexPublicRaw: function () { return self.accountKexPublic; },
        sign: async function (payload) {
          return window.vaultCrypto.toBase64Url(
            await self.vaultSigner.sign(window.vaultCrypto.canonicalCbor(payload))
          );
        },
      };
    },

    // Shared by the direct success path below and its own lost-response
    // recovery branch: both confirm the same envelope landed, and both need
    // a signer and the account's own key-exchange public key for the vault
    // this step is about to create.
    async captureVaultSigningMaterial(sigSeed, kexPrivate, kexPublic) {
      const V = window.vaultCrypto;
      this.vaultSigner = await V.importSigner(sigSeed);
      this.accountKexPublic = V.decodePublicKey(V.fromBase64Url(kexPublic), 'kex');
      sigSeed.fill(0);
      kexPrivate.fill(0);
    },

    // The whole sealing flow, in the order the norm sets out: init for the
    // salt and the account identifier, derive, wrap, attest, finalize.
    async generateAndSeal() {
      const V = window.vaultCrypto;
      this.busy = true;
      this.error = '';
      // init answers 409 only when the identity is already active. On a first
      // attempt that means somewhere else sealed it; on a retry it means our
      // own finalize landed after all, and the reply was what went missing.
      let conflict = false;
      // Declared out here because the catch below reads them: they are the
      // difference between an attempt that drew the keys and a retry that got
      // a 409 before generating anything, and only the first can hand vault
      // signing material to step 3.
      let sigSeed;
      let kexPrivate;
      let kexPublic;
      try {
        const started = await this.post('/api/v1/vault/account/init');
        if (!started.ok) {
          conflict = started.status === 409;
          throw new Error('the server refused to start an account');
        }
        const account = await started.json();
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

        const amk = await V.deriveAmk({
          algo: V.CURRENT_SUITE.kdf.algo,
          password: this.password.normalize('NFC'),
          secretKey: this.secretBytes,
          salt: V.fromBase64Url(account.kdf_salt),
          params: V.CURRENT_SUITE.kdf.params,
        });
        const unwrapKey = await V.hkdf(amk, V.AD.unwrapInfo());

        const kexPair = await crypto.subtle.generateKey('X25519', true, [
          'deriveBits',
        ]);
        const sigPair = await crypto.subtle.generateKey('Ed25519', true, [
          'sign',
          'verify',
        ]);
        kexPrivate = new Uint8Array(
          await crypto.subtle.exportKey('pkcs8', kexPair.privateKey)
        ).slice(-32);
        // WebCrypto exports an Ed25519 private key as PKCS#8 only, and the
        // bundle signs from the bare seed: the last 32 bytes of that fixed
        // structure.
        sigSeed = new Uint8Array(
          await crypto.subtle.exportKey('pkcs8', sigPair.privateKey)
        ).slice(-32);

        kexPublic = V.toBase64Url(
          V.encodePublicKey(
            new Uint8Array(await crypto.subtle.exportKey('raw', kexPair.publicKey)),
            V.CURRENT_SUITE.kexPublicKeyAlg
          )
        );
        const sigPublic = V.toBase64Url(
          V.encodePublicKey(
            new Uint8Array(await crypto.subtle.exportKey('raw', sigPair.publicKey)),
            V.CURRENT_SUITE.sigPublicKeyAlg
          )
        );

        // The unwrap key seals these two as it is - there is no per-ciphertext
        // derivation between them and it - and a password change re-seals them
        // rather than bumping a version. So the header says direct, version
        // zero. The frozen vector account-kex-priv-wrap-format-2 is what these two
        // numbers answer to, and a test holds them against it: nothing at
        // runtime reads the bytes, so nothing else would notice them drift.
        const sealed = {
          keyVersion: 0,
          kdfId: V.KDF_DIRECT,
        };
        const body = {
          kdf_algo: V.CURRENT_SUITE.kdf.algo,
          kdf_params: V.CURRENT_SUITE.kdf.params,
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
        const finalized = await this.post(
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
        const landed = await this.sealedByThisPage();
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
        const response = await fetch('/api/v1/vault/account/envelope', {
          headers: { Accept: 'application/json' },
        });
        if (!response.ok) return 'no';
        const envelope = await response.json();
        if (envelope.state !== 'active') return 'no';
        return envelope.kex_public === this.sentKexPublic ? 'ours' : 'elsewhere';
      } catch (err) {
        return 'no';
      }
    },

    downloadKit() {
      const blob = window.vaultOnboardingTools.buildEmergencyKitPdf({
        email: this.$root.dataset.email,
        serverUrl: window.location.origin,
        secretText: this.groupedSecret(),
        createdAt: new Date().toISOString().slice(0, 10),
      });
      window.downloadBlob(blob, 'vault-emergency-kit.pdf');
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
