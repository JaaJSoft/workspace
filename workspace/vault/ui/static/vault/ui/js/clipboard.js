// Putting a secret on the clipboard, and taking it back.
//
// The clipboard is shared with every other application on the machine, which
// makes both halves delicate:
//
//   - Copying is the one moment a secret is decrypted. Nothing here keeps it:
//     the value is handed to the platform and the reference is dropped.
//   - Clearing is only correct while the clipboard still holds what we put
//     there. The user may have copied something else in the meantime, and
//     wiping then destroys their work to protect a secret that has already
//     gone. So the value is read back and compared before anything is
//     written.
//
// A browser is allowed to refuse that read - Firefox refuses `readText`
// outright - and a refusal is not permission to clear anyway. It leaves the
// clipboard alone and says so, because "your password is still on the
// clipboard" is something the user has to be able to act on.
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

  // Returns what happened, so the caller can tell "cleared" from "left
  // alone" - the second is the one worth telling the user about.
  async function clearIfOurs() {
    if (written === null) return 'nothing';
    const ours = written;
    try {
      const current = await navigator.clipboard.readText();
      if (current !== ours) return 'moved-on';
    } catch (err) {
      return 'refused';
    }
    await navigator.clipboard.writeText('');
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
      // Whatever countdown was running belonged to another value; letting it
      // survive would have it clear this one early, or compare against a
      // secret that is no longer on the clipboard.
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

    // The user asking for it back before the countdown ends. No comparison:
    // they are telling us to, and the value on the clipboard is the one they
    // are looking at.
    cancel: async function () {
      const had = written !== null;
      stop();
      if (had) {
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
