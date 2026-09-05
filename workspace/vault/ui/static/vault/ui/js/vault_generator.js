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

    // Handed to the shared panel as its dependencies. The module's randomness
    // is audited inside the crypto bundle - guards on a missing CSPRNG and on
    // an insecure context, and a source scan that fails on any pseudo-random
    // call - so a password is drawn there rather than from the page.
    generatorDeps() {
      return { randomBytes: (count) => window.vaultCrypto.randomBytes(count) };
    },

    openGenerator(fieldId) {
      this.generatorField = this.generatorField === fieldId ? null : fieldId;
    },

    openGeneratorDialog() {
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
    },

    async copyGenerated(value) {
      // The panel is dismissed on copy, so a rejected write - a denied
      // permission, an unfocused document - would leave the user holding
      // nothing and believing otherwise. Same message as copyField, which
      // fails the same way for the same reason.
      try {
        await window.vaultClipboard.copy('Password', value, { transient: true });
      } catch (err) {
        if (err && err.reason === 'locked') return;
        this.error = 'That value could not be copied.';
      }
    },

    // Takes every generated password back. The panels hold their value in
    // their own state, so closing them is not enough: they are asked to drop
    // it, and they answer wherever they are mounted.
    clearGenerators() {
      this.generatorField = null;
      this.generatorOpen = false;
      window.dispatchEvent(new CustomEvent('password-generator-clear'));
    },
  };
};
