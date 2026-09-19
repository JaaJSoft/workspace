// Password strength meter, for any form where a user chooses a password. The
// Alpine component behind ui/partials/password_strength.html.
//
// The estimator (zxcvbn, in the vendored password-strength bundle) weighs about
// 240 KB gzipped, so it is not loaded with the page: the component fetches it
// the first time the host says the field is on screen, or on the first
// keystroke, whichever comes first. Until it lands, the meter says the
// password is being checked - never that it passed.
//
// Advisory only. The component reports a score and a sentence; whether a
// password is acceptable is the host's floor to set, and the server's
// validators stay the gate either way.

// The score bands zxcvbn defines, 0 to 4.
const PASSWORD_STRENGTH_VERDICTS = [
  { label: 'Very weak', css: 'progress-error', text: 'text-error' },
  { label: 'Weak', css: 'progress-error', text: 'text-error' },
  { label: 'Fair', css: 'progress-warning', text: 'text-warning' },
  { label: 'Strong', css: 'progress-success', text: 'text-success' },
  { label: 'Very strong', css: 'progress-success', text: 'text-success' },
];

// How long a pause counts as "done typing". zxcvbn takes a few milliseconds
// per call, so this is about not flickering the verdict mid-word, not cost.
const PASSWORD_STRENGTH_DEBOUNCE_MS = 250;

let passwordStrengthEstimatorPending = null;

// One script tag per page, however many meters ask. A failed load is not
// remembered: the next keystroke tries again, so a flaky connection costs one
// "could not be checked" rather than the whole session.
function loadPasswordStrengthEstimator(url) {
  if (window.passwordStrengthTools) {
    return Promise.resolve(window.passwordStrengthTools);
  }
  if (!passwordStrengthEstimatorPending) {
    passwordStrengthEstimatorPending = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = url;
      script.onload = () => {
        if (window.passwordStrengthTools) {
          resolve(window.passwordStrengthTools);
        } else {
          reject(new Error('password strength bundle did not register itself'));
        }
      };
      script.onerror = () => reject(new Error('password strength bundle failed to load'));
      document.head.appendChild(script);
    }).catch((err) => {
      passwordStrengthEstimatorPending = null;
      throw err;
    });
  }
  return passwordStrengthEstimatorPending;
}

window.passwordStrengthMeter = function passwordStrengthMeter(bundleUrl) {
  // Bookkeeping, not state: kept out of the reactive object on purpose. The
  // host drives track() from x-effect, and an effect that read a property
  // its own timer later writes would re-run on that write and schedule
  // itself forever.
  //
  // One token per keystroke: an estimate is asynchronous, and the answer
  // about a password the user has already replaced must not overwrite the
  // verdict on the one in the field.
  let generation = 0;
  let debounceTimer = null;

  return {
    // idle | checking | ready | unavailable
    status: 'idle',
    score: null,
    warning: '',
    suggestions: [],

    // Called from x-effect with the host's password and whether the field is
    // on screen, so it re-runs whenever either changes. A visible field warms
    // the estimator before the first keystroke needs it.
    track(password, visible) {
      if (visible) {
        loadPasswordStrengthEstimator(bundleUrl).catch(() => {});
      }
      this.passwordEdited(password);
    },

    passwordEdited(password) {
      generation++;
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = null;
      if (!password) {
        this.reset('idle');
        return;
      }
      // The old verdict is about a different password: take it down now
      // rather than after the debounce.
      this.reset('checking');
      const token = generation;
      debounceTimer = setTimeout(() => {
        debounceTimer = null;
        if (token !== generation) return;
        this.evaluate(password, token);
      }, PASSWORD_STRENGTH_DEBOUNCE_MS);
    },

    async evaluate(password, token) {
      try {
        const tools = await loadPasswordStrengthEstimator(bundleUrl);
        const result = await tools.estimateStrength(password);
        if (token !== generation) return;
        this.score = result.score;
        this.warning = result.warning || '';
        this.suggestions = result.suggestions || [];
        this.status = 'ready';
        this.announce('ready', result.score);
      } catch (err) {
        if (token !== generation) return;
        this.reset('unavailable');
      }
    },

    reset(status) {
      this.status = status;
      this.score = null;
      this.warning = '';
      this.suggestions = [];
      this.announce(status, null);
    },

    // The score never leaves this scope on its own: the meter is a nested
    // component, and a host with a floor of its own cannot read into it. So
    // every state change is announced, and a listener never has to poll.
    //
    // Both values are arguments, and reading them off `this` instead is the
    // one change to avoid: reset() runs inside the host's x-effect, so a read
    // of this.status there makes the effect depend on a property the same
    // effect writes. It then re-runs on its own write, forever - bumping the
    // generation token past every estimate the debounce schedules, which
    // leaves the meter saying "Checking..." and never anything else.
    announce(status, score) {
      this.$dispatch('strength-change', { status, score });
    },

    verdict() {
      if (this.status === 'ready' && this.score !== null) {
        return { ...PASSWORD_STRENGTH_VERDICTS[this.score], value: this.score + 1 };
      }
      let label = '';
      if (this.status === 'checking') label = 'Checking\u2026';
      if (this.status === 'unavailable') label = 'Strength could not be checked on this device';
      return { label, css: '', text: 'text-base-content/60', value: 0 };
    },

    destroy() {
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = null;
      generation++;
    },
  };
};
