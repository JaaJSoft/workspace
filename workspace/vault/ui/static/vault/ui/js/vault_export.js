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
    // The plaintext warning, standing as a step of this dialog rather than as
    // a second dialog over it. Also the gate itself: runExport refuses to
    // write an interchange file while this is false.
    exportConfirming: false,
    exportError: '',
    exportSkipped: 0,
    // A lock is a decision about the run in flight, not only about the dialog.
    // clearExport lands between two of runExport's awaits, and the continuation
    // that resumes after it still holds the tree - it would seal the account
    // under the passphrase that call had just emptied, and write the file to a
    // machine whose vault is closed. This counter is what that continuation
    // reads to know the run it belongs to is over.
    exportGeneration: 0,

    // What the generator panel opens at inside this dialog, pinned rather than
    // left to the panel's own defaults or to what the device last remembered.
    //
    // An archive is the weakest thing this module produces: unlike an account
    // it has no secret key behind it, so the passphrase is the whole of its
    // strength, and the file can be attacked offline for as long as it exists.
    // Eight words off the 1296-word list is ~82 bits, well past the 70 the
    // panel calls Strong. It raises the opening draw; it is not a floor - the
    // sliders still go down, and a typed phrase is measured by nothing at all,
    // which is what the confirmation and the acknowledgement stand in for.
    exportGeneratorOptions() {
      return { mode: 'passphrase', words: 8 };
    },

    // The clipboard clears itself after half a minute, which is the right
    // answer for an entry password: it stays in the vault, so an early clear
    // costs a second Copy and nothing else. This phrase is in no vault. It is
    // the one secret in the module whose loss is final, the warning above the
    // field tells the user to file it with their emergency kit, and a browser
    // save dialog stands between Copy and wherever they are pasting it. Long
    // enough to get through that, still short enough not to leave it lying on
    // a shared clipboard.
    exportClipboardPolicy() {
      return { label: 'Export passphrase', seconds: 180 };
    },

    openExportDialog() {
      this.exportError = '';
      this.exportProgress = 0;
      this.exportSkipped = 0;
      this.exportConfirming = false;
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

    // What the dialog's Export button calls. The archive states its own terms
    // in the form above - a passphrase, and an acknowledgement when the user
    // chose it themselves - so it runs. The interchange file states none: it
    // is every password in the account in the clear, and the only thing
    // between the user and that file is being told so. That warning is a step
    // of this dialog, not a second dialog on top of it: stacked, the two
    // darken the page twice, and Escape closes whichever of them happens to be
    // listening while the other stays.
    requestExport() {
      if (this.exportFormat === 'interchange') {
        this.exportConfirming = true;
        return undefined;
      }
      return this.runExport();
    },

    dismissWarning() {
      this.exportConfirming = false;
    },

    // Methods, never getters: this object is spread into the component, and
    // object spread copies values - a getter would be evaluated once, at
    // spread time, and frozen at whatever the state was then.
    applyGeneratedPassphrase(value) {
      this.exportPassphrase = value;
      this.exportConfirm = value;
      this.exportSource = 'generated';
    },

    // The panel redraws on every option change and on Regenerate, and it
    // announces each draw. A phrase it drew and this field still holds is one
    // the user no longer sees anywhere: the field is masked, so what is on
    // screen is the panel's new draw, and Copy sends that one. Left to drift,
    // the user files away a phrase that opens nothing and the archive is lost
    // exactly as the warning above the field says it would be.
    //
    // Only while the phrase is still the panel's: one the user typed is
    // theirs, and an empty field means they never pressed Use - tracking into
    // it would arm Export with a phrase they never took.
    trackGeneratedPassphrase(value) {
      if (this.exportSource !== 'generated') return;
      if (!this.exportPassphrase) return;
      this.exportPassphrase = value;
      this.exportConfirm = value;
    },

    // Bound to the field's own input: the moment a human edits it, the panel's
    // measurement stops describing what is in there.
    noteTypedPassphrase() {
      // The confirmation field is instantiated by the flip itself, and a
      // generated value left in it is one the user never typed - it would sit
      // there prefilled and no longer matching, holding Export disabled until
      // they think to clear a field they never touched.
      // The acknowledgement goes with them. It is a deliberate statement
      // about the phrase that is about to seal the file, so one ticked for an
      // earlier phrase must not carry over to a different one.
      if (this.exportSource === 'generated') {
        this.exportConfirm = '';
        this.exportOwnPhraseAck = false;
      }
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
      // Checked before anything is decrypted, so a warning that was never
      // answered means nothing was built. State rather than an awaited
      // promise: there is no script that might not have loaded and no window
      // for a lock to land in between the question and the work, and a caller
      // that reached here without the step writes nothing.
      if (this.exportFormat === 'interchange' && !this.exportConfirming) return;
      const generation = this.exportGeneration;
      this.exportBusy = true;
      this.exportError = '';
      this.exportProgress = 0;
      try {
        const tree = await window.vaultExportTree.buildTree(window.vaultSession, {
          // Guarded like every other write below: this callback belongs to the
          // run that passed it in, and a cancelled run goes on walking the
          // account until its own await returns. Unguarded it counts into the
          // run that replaced it, which zeroed the number for itself.
          onProgress: () => {
            if (generation === this.exportGeneration) this.exportProgress += 1;
          },
        });
        // The passphrase is read on the far side of this check and never
        // captured before it: holding a copy across the awaits would keep the
        // phrase alive exactly as long as the lock says it must not be.
        if (generation !== this.exportGeneration) return;
        if (this.exportFormat === 'archive') {
          const bytes = await window.vaultArchive.buildArchive({
            tree: tree,
            passphrase: this.exportPassphrase,
          });
          // Nothing is wrong with these bytes - the sealing started before the
          // lock and finished after it. They still do not reach the disk.
          if (generation !== this.exportGeneration) return;
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
        // A run the lock overtook has nothing to say. Its failure is about an
        // account state nobody is waiting on any more, and the dialog it would
        // write to belongs to whoever opened it next.
        if (generation !== this.exportGeneration) return;
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
        // The one write that runs whichever way the guards above went, so it
        // needs the guard most: releasing the button from a superseded run
        // offers Export again while the live one is still decrypting, and
        // takes "N entries read..." off the screen from under it.
        if (generation === this.exportGeneration) {
          this.exportBusy = false;
          // Back to the form. The dialog stays open over a skipped count, and
          // that count has to be read against the choices that produced it,
          // not against the warning the user already answered.
          this.exportConfirming = false;
        }
      }
    },

    // Called by onLocked, and by closing the dialog. The dialog is mounted
    // under x-if, so dropping the flag tears it down - but the phrase is a JS
    // string and cannot be wiped, so all that is left is letting go of it.
    clearExport() {
      this.exportGeneration += 1;
      this.exportOpen = false;
      this.exportPassphrase = '';
      this.exportConfirm = '';
      this.exportSource = 'generated';
      this.exportOwnPhraseAck = false;
      this.exportProgress = 0;
      this.exportError = '';
      this.exportBusy = false;
      this.exportConfirming = false;
    },
  };
};
