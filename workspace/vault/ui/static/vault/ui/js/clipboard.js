// Putting a secret on the clipboard, and taking it back.
//
// Copying is the one moment a secret is decrypted, and nothing here keeps it:
// the value goes to the platform and the reference is dropped.
//
// Clearing is the delicate half. The clipboard belongs to the whole machine,
// so wiping it is only correct while it still holds what we wrote - the user
// may have copied something else since, and wiping then destroys their work
// to protect a secret that has already gone. So the value is read back and
// compared first. A browser may refuse that read (Firefox refuses readText),
// and a refusal is not permission to clear anyway: it leaves the clipboard
// alone and says so, because "your password is still on the clipboard" is
// something the user has to be able to act on.
window.vaultClipboard = (function () {
  const CLEAR_AFTER_SECONDS = 30;

  let timer = null;
  // What we last wrote, kept only long enough to recognise it on the way out.
  // It is the same secret, so it lives exactly as long as the countdown.
  let written = null;
  let state = { active: false, label: '', secondsLeft: 0, note: '' };
  const listeners = [];

  function publish(next) {
    state = next;
    listeners.forEach(function (callback) { callback(state); });
  }

  function stop() {
    if (timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
    written = null;
  }

  // Returns what happened: "left alone" is the outcome worth reporting.
  async function clearIfOurs() {
    if (written === null) return 'nothing';
    const ours = written;
    try {
      const current = await navigator.clipboard.readText();
      if (current !== ours) return 'moved-on';
      await navigator.clipboard.writeText('');
    } catch (err) {
      // Both halves under the same guard: a write that threw on its own
      // escaped expire(), so the countdown was never stopped and the interval
      // came back a second later to try again, for ever.
      return 'refused';
    }
    return 'cleared';
  }

  async function expire() {
    const outcome = await clearIfOurs();
    stop();
    publish({
      active: false,
      label: '',
      secondsLeft: 0,
      note:
        outcome === 'refused'
          ? 'The clipboard could not be cleared - this browser does not allow reading it back. Copy something else to overwrite it.'
          : '',
    });
  }

  return {
    copy: async function (label, value, options) {
      const settings = options || {};
      // A countdown still running belongs to another value: left alive it
      // would clear this one early.
      stop();
      await navigator.clipboard.writeText(value);
      if (!settings.transient) {
        publish({ active: false, label: '', secondsLeft: 0, note: '' });
        return;
      }
      written = value;
      publish({
        active: true,
        label: label,
        secondsLeft: settings.seconds || CLEAR_AFTER_SECONDS,
        note: '',
      });
      timer = window.setInterval(function () {
        const left = state.secondsLeft - 1;
        if (left > 0) {
          publish({
            active: true,
            label: state.label,
            secondsLeft: left,
            note: '',
          });
          return;
        }
        return expire();
      }, 1000);
    },

    // The user asking for it back before the countdown ends. Asking is not
    // permission to wipe whatever is there now: the banner outlives a copy
    // made somewhere else, so the same comparison the countdown makes runs
    // here, before stop() drops the value it compares against.
    cancel: async function () {
      const outcome = await clearIfOurs();
      stop();
      if (outcome === 'refused') {
        // The read-back was refused, so "is it still ours?" has no answer.
        // The user asked, and on a browser that never allows the comparison
        // this blind write is the only clearing that would ever happen.
        try {
          await navigator.clipboard.writeText('');
        } catch (err) {
          /* nothing to report: the user asked, the platform refused */
        }
      }
      publish({ active: false, label: '', secondsLeft: 0, note: '' });
    },

    state: function () {
      return state;
    },

    onChange: function (callback) {
      listeners.push(callback);
    },
  };
})();
