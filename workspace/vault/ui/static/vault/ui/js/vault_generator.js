// The vault's half of the password generator. The drawing itself is shared
// code (common/ui/js/password_generator.js); what belongs here is everything
// that knows about this module: which byte source to draw from, which
// clipboard to copy through, and when a generated value has to disappear.
window.vaultGeneratorMixin = function vaultGeneratorMixin() {
  return {
    // The field whose generator panel is open, or null. One at a time: two
    // panels would offer two passwords for one entry.
    generatorField: null,
    // The standalone generator, reached from the sidebar.
    generatorOpen: false,
    // Reported next to the panel rather than through `error`: both hosts are
    // modal dialogs, and the page-level alert renders in <main>, behind the
    // backdrop where the user cannot see it.
    generatorError: '',
    // The field the last Use filled, for as long as it still belongs to the
    // panel. The dice folds the panel on Use, and reopening it draws again at
    // once: left on the applied value, the field would be saved with one
    // password while the panel shows, and copies, another. Dropped when the
    // user edits that field or the dialog closes.
    generatorFollows: null,

    // Handed to the shared panel as its dependencies. The module's randomness
    // is audited inside the crypto bundle - guards on a missing CSPRNG and on
    // an insecure context, and a source scan that fails on any pseudo-random
    // call - so a password is drawn there rather than from the page.
    generatorDeps() {
      return { randomBytes: (count) => window.vaultCrypto.randomBytes(count) };
    },

    openGenerator(fieldId) {
      this.generatorError = '';
      this.generatorField = this.generatorField === fieldId ? null : fieldId;
    },

    openGeneratorDialog() {
      this.generatorError = '';
      this.generatorOpen = true;
    },

    closeGeneratorDialog() {
      this.generatorOpen = false;
    },

    applyGenerated(fieldId, value) {
      // The dialog can close under an open panel - a lock does exactly that -
      // so the draft is checked rather than assumed.
      if (this.draft && this.draft.values) this.draft.values[fieldId] = value;
      this.generatorField = null;
      this.generatorFollows = fieldId;
    },

    // A failed draw announces an empty value, and emptying a password the
    // user already applied would be worse than leaving it next to an error.
    trackGenerated(fieldId, value) {
      if (this.generatorFollows !== fieldId || !value) return;
      if (this.draft && this.draft.values) this.draft.values[fieldId] = value;
    },

    noteEditedField(fieldId) {
      if (this.generatorFollows === fieldId) this.generatorFollows = null;
    },

    // The label and the clearing window belong to the host. This mixin serves
    // the entry dialog, the standalone generator and the export dialog, and
    // only the last of those copies something no vault can hand back.
    async copyGenerated(value, options) {
      const policy = options || {};
      // Copying is the only way a value drawn here leaves the dialog, and
      // closing the dialog drops it: a rejected write - a denied permission,
      // an unfocused document - has to say so next to the button that was
      // pressed, or the user closes on an empty clipboard believing otherwise.
      try {
        await window.vaultClipboard.copy(policy.label || 'Password', value, {
          transient: true,
          seconds: policy.seconds,
        });
        this.generatorError = '';
      } catch (err) {
        this.generatorError = 'That value could not be copied.';
      }
    },

    // Takes every generated password off the screen. Both panels are mounted
    // under x-if, so dropping the flags tears them down and runs the destroy()
    // that lets go of what they hold.
    clearGenerators() {
      this.generatorField = null;
      this.generatorFollows = null;
      this.generatorOpen = false;
      this.generatorError = '';
    },
  };
};
