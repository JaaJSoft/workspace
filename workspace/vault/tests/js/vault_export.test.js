const test = require('node:test');
const assert = require('node:assert');
const { loadScript, loadScripts } = require('../../../common/tests/js/loader');

const SCRIPT = 'workspace/vault/ui/static/vault/ui/js/vault_export.js';
const WORDLIST = 'workspace/common/static/ui/js/password_wordlist.js';
const GENERATOR = 'workspace/common/static/ui/js/password_generator.js';
// An archive has no secret key behind it and is attacked offline for as long
// as it exists, so the passphrase this dialog draws carries the whole of its
// strength. 72 bits is past the panel's own Strong band; the design asked for
// eight words, which is ~82.
const ARCHIVE_BAR_BITS = 72;

function load(overrides = {}) {
  const downloads = [];
  const ctx = loadScript(SCRIPT, Object.assign({
    downloadBlob: (blob, filename) => downloads.push(filename),
    vaultExportTree: { buildTree: async () => ({ format: 'vault-archive', vaults: [] }) },
    vaultArchive: {
      buildArchive: async () => new Uint8Array([1, 2, 3]),
      archiveFilename: () => 'vault-export-2026-09-06.vaultarchive',
    },
    vaultExportInterchange: {
      toBitwarden: () => ({ json: { encrypted: false }, skipped: 0 }),
      interchangeFilename: () => 'vault-export-2026-09-06.json',
    },
    Blob: function Blob(parts, options) { this.parts = parts; this.options = options; },
  }, overrides));
  return { component: ctx.vaultExportMixin(), downloads };
}

// The plaintext warning, answered the way the dialog answers it.
function throughWarning(component) {
  component.requestExport();
  return component.runExport();
}

test('a generated passphrase is accepted with no further ceremony', () => {
  // Its strength is known exactly - the panel computed it from the request it
  // drew, which is the only thing entropyBits can measure.
  const { component } = load();
  component.exportFormat = 'archive';
  component.applyGeneratedPassphrase('correcte cheval batterie agrafe sept huit neuf huit');
  assert.equal(component.exportSource, 'generated');
  assert.equal(component.passphraseAccepted(), true);
});

test('a typed passphrase needs its confirmation and an explicit acknowledgement', () => {
  // We cannot measure what a human chose, so we do not pretend to. What we can
  // do is make choosing it deliberate, and catch a typo in a masked field.
  const { component } = load();
  component.exportFormat = 'archive';
  component.exportPassphrase = 'ma phrase a moi';
  component.noteTypedPassphrase();
  assert.equal(component.exportSource, 'typed');
  assert.equal(component.passphraseAccepted(), false, 'accepted with no confirmation');
  component.exportConfirm = 'ma phrase a moi';
  assert.equal(component.passphraseAccepted(), false, 'accepted without the acknowledgement');
  component.exportOwnPhraseAck = true;
  assert.equal(component.passphraseAccepted(), true);
  component.exportConfirm = 'ma phrase a mol';
  assert.equal(component.passphraseAccepted(), false, 'accepted a mistyped confirmation');
});

test('an empty passphrase is refused whatever its source', () => {
  const { component } = load();
  component.exportFormat = 'archive';
  component.applyGeneratedPassphrase('');
  assert.equal(component.passphraseAccepted(), false);
});

test('the plaintext warning is a step of this dialog, not a second one on top', () => {
  // Stacked, the two darken the page twice and answer Escape with whichever
  // of them is listening - and the archive branch already takes its
  // acknowledgement here. One dialog, one gesture.
  const { component } = load();
  component.exportOpen = true;
  component.exportFormat = 'interchange';
  component.requestExport();
  assert.equal(component.exportConfirming, true);
  assert.equal(component.exportOpen, true, 'the dialog the warning belongs to closed');
});

test('the warning comes before anything is decrypted', () => {
  // Refusing must mean nothing was built, not "we decrypted it all and then
  // threw it away".
  let built = 0;
  const { component, downloads } = load({
    vaultExportTree: { buildTree: async () => { built += 1; return { vaults: [] }; } },
  });
  component.exportFormat = 'interchange';
  component.requestExport();
  assert.equal(built, 0, 'the tree was built before the warning was answered');
  component.dismissWarning();
  assert.equal(component.exportConfirming, false);
  assert.equal(built, 0, 'the tree was built despite the refusal');
  assert.equal(downloads.length, 0);
});

test('nothing writes a plaintext file without passing the warning', async () => {
  // The gate is state this component owns rather than a promise from a script
  // that may not have loaded, so it reads the same whatever else is on the
  // page - and a path that skipped the step writes nothing.
  const { component, downloads } = load();
  component.exportFormat = 'interchange';
  await component.runExport();
  assert.equal(downloads.length, 0);
  assert.equal(component.exportConfirming, false);
});

test('the interchange export downloads once the warning is accepted', async () => {
  const { component, downloads } = load();
  component.exportFormat = 'interchange';
  await throughWarning(component);
  assert.deepStrictEqual(downloads, ['vault-export-2026-09-06.json']);
});

test('the archive is not held behind the plaintext warning', async () => {
  // It has a gate of its own - a passphrase, and an acknowledgement when the
  // user chose it. This warning belongs to the format that has none.
  const { component, downloads } = load();
  component.exportFormat = 'archive';
  component.applyGeneratedPassphrase('correcte cheval batterie agrafe sept huit neuf huit');
  await component.requestExport();
  assert.equal(component.exportConfirming, false);
  assert.deepStrictEqual(downloads, ['vault-export-2026-09-06.vaultarchive']);
});

test('a finished run takes the warning step back down', async () => {
  // The dialog stays open over a skipped count and has to come back to the
  // form to show it, not to the red step the user already answered.
  const { component } = load({
    vaultExportInterchange: {
      toBitwarden: () => ({ json: { encrypted: false }, skipped: 2 }),
      interchangeFilename: () => 'vault-export-2026-09-06.json',
    },
  });
  component.exportOpen = true;
  component.exportFormat = 'interchange';
  await throughWarning(component);
  assert.equal(component.exportOpen, true);
  assert.equal(component.exportConfirming, false, 'the dialog stayed on the warning');
});

test('locking takes the warning step down with the rest', () => {
  const { component } = load();
  component.exportFormat = 'interchange';
  component.requestExport();
  component.clearExport();
  assert.equal(component.exportConfirming, false);
});

test('entries the format cannot carry are named, and the dialog stays to say so', async () => {
  // The count is the whole point of computing it. Closing over it would
  // compute a number and throw it away, which looks implemented and is not.
  const { component, downloads } = load({
    vaultExportInterchange: {
      toBitwarden: () => ({ json: { encrypted: false }, skipped: 3 }),
      interchangeFilename: () => 'vault-export-2026-09-06.json',
    },
  });
  component.exportOpen = true;
  component.exportFormat = 'interchange';
  await throughWarning(component);
  assert.equal(downloads.length, 1, 'the file was withheld over a count');
  assert.equal(component.exportOpen, true, 'the dialog closed over the count');
  assert.equal(component.exportSkipped, 3);
  assert.match(component.skippedMessage(), /3 entries/);
});

test('a run reports its own outcome, never the one before it', async () => {
  // The dialog now stays open over a skipped count, so a second run happens on
  // a component the first one left state on. An archive skips nothing and has
  // no notion of skipping: inheriting the count would hold the dialog open on
  // a sentence about a file that was never written that way.
  const { component, downloads } = load({
    vaultExportInterchange: {
      toBitwarden: () => ({ json: { encrypted: false }, skipped: 3 }),
      interchangeFilename: () => 'vault-export-2026-09-06.json',
    },
  });
  component.exportOpen = true;
  component.exportFormat = 'interchange';
  await throughWarning(component);
  assert.equal(component.exportSkipped, 3);
  assert.equal(component.exportOpen, true, 'the count had nowhere to be read');

  component.exportFormat = 'archive';
  component.applyGeneratedPassphrase('correcte cheval batterie agrafe sept huit neuf huit');
  await component.runExport();
  assert.deepStrictEqual(downloads, [
    'vault-export-2026-09-06.json',
    'vault-export-2026-09-06.vaultarchive',
  ]);
  assert.equal(component.exportSkipped, 0, 'the archive run inherited the interchange count');
  assert.equal(component.exportOpen, false, 'the dialog stayed open over a stale count');
});

test('nothing skipped closes the dialog as before', async () => {
  const { component } = load();
  component.exportOpen = true;
  component.exportFormat = 'interchange';
  await throughWarning(component);
  assert.equal(component.exportOpen, false);
  assert.equal(component.skippedMessage(), '');
});

test('editing over a generated phrase does not leave it in the confirmation', async () => {
  // The confirmation field is instantiated by the flip itself. Prefilled with
  // the value being replaced, it holds Export disabled over a mismatch the
  // user never typed.
  const { component } = load();
  component.applyGeneratedPassphrase('correcte cheval batterie agrafe sept huit neuf huit');
  assert.equal(component.exportConfirm, 'correcte cheval batterie agrafe sept huit neuf huit');
  component.exportPassphrase = 'ma phrase a moi';
  component.noteTypedPassphrase();
  assert.equal(component.exportConfirm, '', 'the generated value survived the flip');
  // And a second keystroke does not wipe what the user has since confirmed.
  component.exportConfirm = 'ma phrase a moi';
  component.noteTypedPassphrase();
  assert.equal(component.exportConfirm, 'ma phrase a moi');
});

test('the acknowledgement does not carry from one phrase to the next', async () => {
  // It is a deliberate statement about the phrase that is about to seal the
  // file. Ticked for a typed phrase, then a draw, then an edit: left standing
  // it would speak for a phrase the user never acknowledged.
  const { component } = load();
  component.exportFormat = 'archive';
  component.exportPassphrase = 'ma premiere phrase';
  component.noteTypedPassphrase();
  component.exportConfirm = 'ma premiere phrase';
  component.exportOwnPhraseAck = true;
  assert.equal(component.passphraseAccepted(), true);

  component.applyGeneratedPassphrase('correcte cheval batterie agrafe sept huit neuf huit');
  component.exportPassphrase = 'ma seconde phrase';
  component.noteTypedPassphrase();
  assert.equal(component.exportOwnPhraseAck, false, 'the earlier tick survived');
  component.exportConfirm = 'ma seconde phrase';
  assert.equal(
    component.passphraseAccepted(), false,
    'a confirmed phrase was accepted on an acknowledgement given for another'
  );
});

test('an unreadable account reports it and downloads nothing', async () => {
  const { component, downloads } = load({
    vaultExportTree: {
      buildTree: async () => {
        const error = new Error('nope');
        error.reason = 'unreadable';
        throw error;
      },
    },
  });
  component.exportFormat = 'archive';
  component.applyGeneratedPassphrase('correcte cheval batterie agrafe sept huit neuf huit');
  await component.runExport();
  assert.equal(downloads.length, 0);
  assert.match(component.exportError, /could not be read/i);
});

test('locking drops the dialog and the phrase it holds', () => {
  const { component } = load();
  component.exportOpen = true;
  component.applyGeneratedPassphrase('correcte cheval batterie agrafe sept huit neuf huit');
  component.exportOwnPhraseAck = true;
  component.clearExport();
  assert.equal(component.exportOpen, false);
  assert.equal(component.exportPassphrase, '');
  assert.equal(component.exportConfirm, '');
  assert.equal(component.exportOwnPhraseAck, false);
});

test('the export dialog opens its generator past the archive bar', () => {
  // Composed from the real panel and the real wordlist rather than asserting
  // on the numbers in exportGeneratorOptions(): what matters is the entropy
  // the panel ends up reporting, and that depends on both.
  //
  // Storage is seeded with the weakest thing the entry dialog could have left
  // behind, because that is the case the pinning exists for - the stored
  // options are shared by every host.
  const entries = new Map([
    ['passwordGenerator.options', JSON.stringify({
      mode: 'password', length: 8, upper: false, digits: false, symbols: false, words: 3,
    })],
  ]);
  const ctx = loadScripts([WORDLIST, GENERATOR, SCRIPT], {
    crypto: globalThis.crypto,
    localStorage: {
      getItem: (key) => (entries.has(key) ? entries.get(key) : null),
      setItem: (key, value) => entries.set(key, String(value)),
      removeItem: (key) => entries.delete(key),
    },
  });
  const panel = ctx.passwordGeneratorPanel({}, ctx.vaultExportMixin().exportGeneratorOptions());
  panel.$watch = () => {};
  panel.$dispatch = () => {};
  panel.init();

  assert.equal(panel.error, '');
  assert.ok(
    panel.bits >= ARCHIVE_BAR_BITS,
    `the export dialog opens at ${panel.bits.toFixed(1)} bits, under ${ARCHIVE_BAR_BITS}`
  );
  // The value on screen is the one that was measured: a request of eight words
  // that drew six would report a strength the file does not have.
  assert.equal(panel.mode, 'passphrase');
  assert.equal(panel.value.split(panel.separator).length, panel.words);
});

test('a lock between the tree and the sealing withholds the file', async () => {
  // clearExport empties the passphrase, and the run that was already past its
  // first await goes on holding the tree. Left alone it seals the account
  // under the empty string and writes it to the disk of a machine whose vault
  // is closed - a file weaker than the account it copies.
  let lock = () => {};
  const sealed = [];
  const { component, downloads } = load({
    vaultExportTree: {
      buildTree: async () => { lock(); return { format: 'vault-archive', vaults: [] }; },
    },
    vaultArchive: {
      buildArchive: async (args) => { sealed.push(args.passphrase); return new Uint8Array([1, 2, 3]); },
      archiveFilename: () => 'vault-export-2026-09-06.vaultarchive',
    },
  });
  lock = () => component.clearExport();
  component.exportFormat = 'archive';
  component.applyGeneratedPassphrase('correcte cheval batterie agrafe sept huit neuf huit');
  await component.runExport();
  assert.deepStrictEqual(sealed, [], 'the archive was sealed after the lock');
  assert.equal(downloads.length, 0, 'a file was written after the lock');
});

test('a lock while the archive is sealed withholds the bytes it produced', async () => {
  // The other side of the same await: the sealing started before the lock and
  // finished after it, so the bytes exist and nothing is wrong with them. They
  // still must not reach the disk - the user closed the vault in between.
  let lock = () => {};
  const { component, downloads } = load({
    vaultArchive: {
      buildArchive: async () => { lock(); return new Uint8Array([1, 2, 3]); },
      archiveFilename: () => 'vault-export-2026-09-06.vaultarchive',
    },
  });
  lock = () => component.clearExport();
  component.exportFormat = 'archive';
  component.applyGeneratedPassphrase('correcte cheval batterie agrafe sept huit neuf huit');
  await component.runExport();
  assert.equal(downloads.length, 0, 'a file was written after the lock');
});

// Wires a real panel to a real mixin the way export_dialog.html does, so the
// events under test are the ones the browser will actually deliver.
function wirePanel(entries = new Map()) {
  const ctx = loadScripts([WORDLIST, GENERATOR, SCRIPT], {
    crypto: globalThis.crypto,
    localStorage: {
      getItem: (key) => (entries.has(key) ? entries.get(key) : null),
      setItem: (key, value) => entries.set(key, String(value)),
      removeItem: (key) => entries.delete(key),
    },
  });
  const component = ctx.vaultExportMixin();
  const copied = [];
  const panel = ctx.passwordGeneratorPanel({}, component.exportGeneratorOptions());
  panel.$watch = () => {};
  // The template's handlers, by name. A dispatch the mixin has no handler for
  // is left alone rather than throwing, so a missing handler fails on the
  // state it should have produced instead of on a TypeError.
  panel.$dispatch = (name, detail) => {
    if (name === 'password-apply') component.applyGeneratedPassphrase(detail.value);
    if (name === 'password-copy') copied.push(detail.value);
    if (name === 'password-regenerate' && component.trackGeneratedPassphrase) {
      component.trackGeneratedPassphrase(detail.value);
    }
  };
  panel.init();
  return { component, panel, copied };
}

test('a redraw does not leave the field holding the phrase before it', () => {
  // The panel keeps drawing after Use - every option change fires its watcher,
  // and Regenerate does it outright. The field is type="password", so a value
  // left behind by a redraw is invisible: the user reads the panel, copies
  // what the panel shows, and seals the archive with what the field kept.
  // Nothing then opens the file, and the dialog has already said that losing
  // the phrase loses the archive with it.
  const { component, panel } = wirePanel();
  panel.apply();
  assert.equal(component.exportPassphrase, panel.value, 'Use did not fill the field');

  panel.regenerate();
  assert.equal(
    component.exportPassphrase, panel.value,
    'the field kept the phrase the panel had already replaced'
  );
});

test('the phrase that is copied is the phrase that seals the file', () => {
  // Copy sends the panel's current value. The whole failure is the two
  // drifting apart, so pin them against each other through the real Copy.
  const { component, panel, copied } = wirePanel();
  panel.apply();
  panel.regenerate();
  panel.copy();
  assert.deepStrictEqual(copied, [component.exportPassphrase]);
});

test('a redraw does not overwrite a phrase the user typed', () => {
  // Tracking follows a phrase the panel drew. One a human chose is theirs,
  // and a stray click on Regenerate must not take it away.
  const { component, panel } = wirePanel();
  panel.apply();
  component.exportPassphrase = 'ma phrase a moi';
  component.noteTypedPassphrase();
  panel.regenerate();
  assert.equal(component.exportPassphrase, 'ma phrase a moi');
  assert.equal(component.exportSource, 'typed');
});

test('a redraw before Use fills nothing', () => {
  // The panel draws on init and on every option change. Tracking those into
  // an untouched dialog would arm Export with a phrase the user never took,
  // and never saw themselves take.
  const { component, panel } = wirePanel();
  panel.regenerate();
  assert.equal(component.exportPassphrase, '');
  assert.equal(component.passphraseAccepted(), false);
});

const PHRASE = 'correcte cheval batterie agrafe sept huit neuf huit';
const tick = () => new Promise((resolve) => setImmediate(resolve));

// A buildTree that parks until the test lets it through, so a run can be left
// mid-flight while the next one starts. Cancelling does not unwind the run in
// flight - it only stops it from mattering - so this is the shape every
// generation guard exists for.
function gatedTree() {
  const gates = [];
  return {
    gates,
    buildTree: (session, options) => {
      let release;
      let fail;
      const parked = new Promise((resolve, reject) => { release = resolve; fail = reject; });
      gates.push({ release, fail, options });
      return parked.then(() => {
        if (options && options.onProgress) options.onProgress();
        return { format: 'vault-archive', vaults: [] };
      });
    },
  };
}

// Arms a second run on a component whose first one is still parked.
function supersede(component) {
  component.clearExport();
  component.exportOpen = true;
  component.exportFormat = 'archive';
  component.applyGeneratedPassphrase(PHRASE);
  return component.runExport();
}

test('a cancelled run does not release the button of the run that replaced it', async () => {
  // The generation guard covers every branch inside the try, but the finally
  // runs whichever way the guard went. A run the user cancelled then reaches
  // its end while a second one is decrypting, and hands the button back: the
  // dialog offers Export again mid-run, and "N entries read..." disappears
  // from under a run that is still going.
  const tree = gatedTree();
  const { component } = load({ vaultExportTree: { buildTree: tree.buildTree } });
  component.exportOpen = true;
  component.exportFormat = 'archive';
  component.applyGeneratedPassphrase(PHRASE);
  const first = component.runExport();
  await tick();
  assert.equal(component.exportBusy, true);

  const second = supersede(component);
  await tick();
  assert.equal(component.exportBusy, true, 'the second run never started');

  tree.gates[0].release();
  await first;
  assert.equal(component.exportBusy, true, 'the cancelled run freed the button mid-run');

  tree.gates[1].release();
  await second;
  assert.equal(component.exportBusy, false);
});

test('a cancelled run does not report its failure on the dialog that replaced it', async () => {
  // Same asymmetry on the other exit: the catch writes exportError without
  // asking which run it belongs to, so a failure from a run nobody is waiting
  // on any more paints "The export failed." over a freshly opened dialog.
  const tree = gatedTree();
  const { component } = load({ vaultExportTree: { buildTree: tree.buildTree } });
  component.exportOpen = true;
  component.exportFormat = 'archive';
  component.applyGeneratedPassphrase(PHRASE);
  const first = component.runExport();
  await tick();

  const second = supersede(component);
  await tick();

  tree.gates[0].fail(Object.assign(new Error('nope'), { reason: 'unreadable' }));
  await first;
  assert.equal(component.exportError, '', 'a dead run reported itself on the live dialog');

  tree.gates[1].release();
  await second;
});

test('a cancelled run stops counting into the run that replaced it', async () => {
  // onProgress is captured by the run that passed it in and keeps firing after
  // the cancellation. The replacement zeroed the counter for itself, so the
  // number on screen ends up describing neither run.
  const tree = gatedTree();
  const { component } = load({ vaultExportTree: { buildTree: tree.buildTree } });
  component.exportOpen = true;
  component.exportFormat = 'archive';
  component.applyGeneratedPassphrase(PHRASE);
  const first = component.runExport();
  await tick();

  const second = supersede(component);
  await tick();

  tree.gates[0].release();
  await first;
  assert.equal(component.exportProgress, 0, 'the cancelled run counted into the live one');

  tree.gates[1].release();
  await second;
});

test('the archive passphrase outlives the clipboard window an entry password gets', () => {
  // Against the real clipboard rather than a number written twice: what
  // matters is that this phrase gets longer than the default, whatever the
  // default becomes.
  const clip = { value: null };
  const ctx = loadScripts(
    [
      'workspace/vault/ui/static/vault/ui/js/clipboard.js',
      'workspace/vault/ui/static/vault/ui/js/vault_export.js',
    ],
    {
      navigator: { clipboard: { writeText: async (text) => { clip.value = text; } } },
      setInterval: () => 1,
      clearInterval: () => {},
    }
  );
  const policy = ctx.vaultExportMixin().exportClipboardPolicy();

  return ctx.vaultClipboard.copy('Password', 'entry', { transient: true })
    .then(() => {
      const entryWindow = ctx.vaultClipboard.state().secondsLeft;
      return ctx.vaultClipboard
        .copy(policy.label, 'phrase', { transient: true, seconds: policy.seconds })
        .then(() => {
          const archiveWindow = ctx.vaultClipboard.state().secondsLeft;
          assert.ok(
            archiveWindow > entryWindow,
            `the phrase gets ${archiveWindow}s, an entry password ${entryWindow}s`
          );
          assert.equal(ctx.vaultClipboard.state().active, true, 'the clearing was turned off');
          assert.equal(ctx.vaultClipboard.state().label, 'Export passphrase');
        });
    });
});
