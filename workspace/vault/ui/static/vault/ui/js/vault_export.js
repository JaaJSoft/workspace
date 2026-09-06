// The vault's export dialog. What belongs here is what knows about this
// module: which passphrase protects the archive, what the user is told before
// a plaintext file is built, and when both go away.
window.vaultExportMixin = function vaultExportMixin() {
  return {
    exportOpen: false,
    exportFormat: 'archive',
    exportPassphrase: '',
    exportConfirm: '',
    // 'generated' means the panel drew it and its strength is known exactly.
    // 'typed' means a human chose it, and nothing here can measure that.
    exportSource: 'generated',
    exportOwnPhraseAck: false,
    exportProgress: 0,
    exportBusy: false,
    exportError: '',
    exportSkipped: 0,

    openExportDialog() {
      this.exportError = '';
      this.exportProgress = 0;
      this.exportSkipped = 0;
      // The panel inside this dialog reports a refused copy through the
      // generator mixin's field, spread into this same component - a failure
      // left over from the standalone generator would open here as if it had
      // just happened.
      this.generatorError = '';
      this.exportOpen = true;
    },

    closeExportDialog() {
      this.clearExport();
    },

    // Methods, never getters: this object is spread into the component, and
    // object spread copies values - a getter would be evaluated once, at
    // spread time, and frozen at whatever the state was then.
    applyGeneratedPassphrase(value) {
      this.exportPassphrase = value;
      this.exportConfirm = value;
      this.exportSource = 'generated';
    },

    // Bound to the field's own input: the moment a human edits it, the panel's
    // measurement stops describing what is in there.
    noteTypedPassphrase() {
      // The confirmation field is instantiated by the flip itself, and a
      // generated value left in it is one the user never typed - it would sit
      // there prefilled and no longer matching, holding Export disabled until
      // they think to clear a field they never touched.
      if (this.exportSource === 'generated') this.exportConfirm = '';
      this.exportSource = 'typed';
    },

    // A count, not a secret: it outlives the download so the dialog can say
    // what was left out, and openExportDialog() clears it for the next run.
    skippedMessage() {
      if (!this.exportSkipped) return '';
      if (this.exportSkipped === 1) {
        return 'One entry has no counterpart in this format, so it was not '
          + 'written to the file. It is still in your account.';
      }
      return this.exportSkipped + ' entries have no counterpart in this format, '
        + 'so they were not written to the file. They are still in your account.';
    },

    // The archive is attacked offline for as long as the file exists, with
    // nothing but this phrase in front of it - the account also has a 32-byte
    // secret key, and an export has none. The counterweight is on this side.
    //
    // A generated phrase carries a known count, drawn by the panel from a
    // request entropyBits can actually measure, so it needs nothing further.
    // A phrase a human chose cannot be measured at all - entropy is a property
    // of the process that produced it, not of the string - so instead of
    // showing an invented number we ask for a confirmation (the field is
    // masked, and a typo locks the archive forever) and a deliberate
    // acknowledgement.
    passphraseAccepted() {
      if (!this.exportPassphrase) return false;
      if (this.exportSource === 'generated') return true;
      return this.exportPassphrase === this.exportConfirm && this.exportOwnPhraseAck;
    },

    async runExport() {
      if (this.exportBusy) return;
      if (this.exportFormat === 'archive' && !this.passphraseAccepted()) return;
      // Each attempt reports its own outcome and nothing else. The dialog no
      // longer closes on every success, so a count left by a previous run
      // survives into the next one - and an archive run, which skips nothing
      // and has no notion of skipping, would end up displaying it.
      this.exportSkipped = 0;
      // Before anything is decrypted, so cancelling means nothing was built.
      //
      // this.confirm, from the component root: dialogs.js declares AppDialog
      // with a top-level `const`, which never becomes a property of window, so
      // window.AppDialog is undefined. And the option is okLabel - an invented
      // confirmLabel would leave the button reading "OK" with nothing to say so.
      if (this.exportFormat === 'interchange') {
        // This one caller fails closed where the shared wrapper fails open.
        // `confirm` answers true when dialogs.js has not loaded, which is the
        // right default for a destructive action the user already asked for -
        // and the wrong one here, because this confirm *is* the warning that a
        // file holding every password in the clear is about to be written. A
        // warning nobody could see was never accepted.
        //
        // The bare identifier, never window.AppDialog: dialogs.js declares it
        // with a top-level `const`, which never becomes a property of window.
        if (typeof AppDialog === 'undefined') {
          this.exportError = 'This export could not be confirmed, so nothing was written.';
          return;
        }
        const accepted = await this.confirm(
          'It contains every password in this account in plain text. Anyone who '
          + 'opens the file can read them. Nothing encrypts it.',
          { title: 'This file is not protected', okLabel: 'Export anyway', okClass: 'btn-error' }
        );
        if (!accepted) return;
      }
      this.exportBusy = true;
      this.exportError = '';
      this.exportProgress = 0;
      try {
        const tree = await window.vaultExportTree.buildTree(window.vaultSession, {
          onProgress: () => { this.exportProgress += 1; },
        });
        if (this.exportFormat === 'archive') {
          const bytes = await window.vaultArchive.buildArchive({
            tree: tree,
            passphrase: this.exportPassphrase,
          });
          window.downloadBlob(
            new Blob([bytes], { type: 'application/octet-stream' }),
            window.vaultArchive.archiveFilename(new Date())
          );
        } else {
          const { json, skipped } = window.vaultExportInterchange.toBitwarden(tree);
          this.exportSkipped = skipped;
          window.downloadBlob(
            new Blob([JSON.stringify(json, null, 2)], { type: 'application/json' }),
            window.vaultExportInterchange.interchangeFilename(new Date())
          );
        }
        // The dialog closes on its own only when it has nothing left to say.
        // A count the projection dropped needs somewhere to be read, and
        // closing over it would compute the number and throw it away.
        if (!this.exportSkipped) this.clearExport();
      } catch (err) {
        if (err && err.reason === 'unreadable') {
          this.exportError =
            'Part of this account could not be read, so no file was written. '
            + 'A partial backup is worse than none.';
        } else if (err && err.reason === 'empty') {
          this.exportError = 'There is nothing to export yet.';
        } else if (err && err.reason === 'locked') {
          this.exportError = 'The vault locked before the export finished.';
        } else {
          this.exportError = 'The export failed.';
        }
      } finally {
        this.exportBusy = false;
      }
    },

    // Called by onLocked, and by closing the dialog. The dialog is mounted
    // under x-if, so dropping the flag tears it down - but the phrase is a JS
    // string and cannot be wiped, so all that is left is letting go of it.
    clearExport() {
      this.exportOpen = false;
      this.exportPassphrase = '';
      this.exportConfirm = '';
      this.exportSource = 'generated';
      this.exportOwnPhraseAck = false;
      this.exportProgress = 0;
      this.exportError = '';
      this.exportBusy = false;
    },
  };
};
