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
      this.exportSource = 'typed';
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
      // Before anything is decrypted, so cancelling means nothing was built.
      //
      // this.confirm, from the component root: dialogs.js declares AppDialog
      // with a top-level `const`, which never becomes a property of window, so
      // window.AppDialog is undefined. And the option is okLabel - an invented
      // confirmLabel would leave the button reading "OK" with nothing to say so.
      if (this.exportFormat === 'interchange') {
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
        this.clearExport();
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
