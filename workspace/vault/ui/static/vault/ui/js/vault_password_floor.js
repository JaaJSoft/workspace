// The floor a vault password must clear, wherever one is chosen: onboarding
// and the password change. Twelve code points after NFC, a zxcvbn score of at
// least three from a landed estimate, and absence from the breach corpus. No
// composition rules. The server cannot check any of it - it never sees the
// password - so this is the only place it is enforced.
const VAULT_MIN_PASSWORD_LENGTH = 12;
const VAULT_MIN_PASSWORD_SCORE = 3;

window.vaultPasswordFloorMixin = function vaultPasswordFloorMixin() {
  return {
    password: '',
    confirmation: '',
    score: null,
    // unchecked | checking | clean | found | unavailable
    breachStatus: 'unchecked',
    // One token per corpus lookup. The answer takes as long as the network
    // wants: without it, a reply about a password the user has already
    // replaced can overwrite the verdict on the one in the field, and a stale
    // "clean" lets a breached password through the floor. The meter discards
    // its own stale estimates on its side.
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

    // x-model writes the field through on every keystroke while the corpus
    // lookup waits out its debounce. Without this the floor would keep
    // reporting the previous password's verdict for those 400 ms.
    //
    // The score is deliberately not cleared here, and clearing it is the one
    // change to avoid: it belongs to the meter, which speaks from an effect,
    // and an Alpine effect does not re-run when a property is assigned the
    // value it already holds. A password manager re-filling the field with
    // what is already in it is exactly that - an input event with nothing to
    // change - so a score cleared here would never come back. Nothing is
    // stale in the meantime either: the line below shuts the floor on every
    // input whatever the score says, until the corpus has answered again.
    passwordEdited() {
      this.generation++;
      this.breachStatus = 'unchecked';
    },

    passwordChanged() {
      this.generation++;
      this.checkBreachCorpus();
    },

    // The meter under the field owns the estimate - one component for both
    // pages that measure a password - and reports it here. Anything short of a
    // landed estimate is an unmeasured password, which the floor refuses: an
    // estimator that never loaded says so on screen and must not open the step
    // it could not vouch for.
    strengthChanged(detail) {
      this.score = detail && detail.status === 'ready' ? detail.score : null;
    },

    // k-anonymity: only the first five hex characters of the SHA-1 leave the
    // device, and the answer is a list of suffixes we match locally. The
    // password itself never crosses the network.
    async checkBreachCorpus() {
      const generation = this.generation;
      if (!this.password) {
        this.breachStatus = 'unchecked';
        return;
      }
      this.breachStatus = 'checking';
      try {
        // SHA-1 through WebCrypto rather than through the bundle: the corpus
        // is indexed by it, and adding a hash nobody else needs would spend
        // the main bundle's byte budget on one caller.
        const bytes = new TextEncoder().encode(this.password);
        const digest = Array.from(
          new Uint8Array(await crypto.subtle.digest('SHA-1', bytes))
        )
          .map(function (byte) {
            return byte.toString(16).padStart(2, '0');
          })
          .join('')
          .toUpperCase();
        const prefix = digest.slice(0, 5);
        const suffix = digest.slice(5);
        const response = await fetch(
          'https://api.pwnedpasswords.com/range/' + prefix
        );
        if (!response.ok) throw new Error('breach lookup failed');
        const body = await response.text();
        const found = body.split('\n').some(function (line) {
          return line.split(':')[0].trim().toUpperCase() === suffix;
        });
        if (generation !== this.generation) return;
        this.breachStatus = found ? 'found' : 'clean';
      } catch (err) {
        if (generation !== this.generation) return;
        this.breachStatus = 'unavailable';
      }
    },
  };
};
