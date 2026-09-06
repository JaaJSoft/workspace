const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const SCRIPT = 'workspace/vault/ui/static/vault/ui/js/vault_export.js';

function load(overrides = {}) {
  const downloads = [];
  const asked = [];
  const ctx = loadScript(SCRIPT, Object.assign({
    // Present, not called: the component's own `confirm` wrapper is stubbed
    // below. What the mixin reads off this is whether dialogs.js loaded at
    // all - as the bare identifier, the way the browser sees it.
    AppDialog: { confirm: async () => true },
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
  // `confirm` comes from the component root (vault_browser.js), not from this
  // mixin: the mixin is spread into that component. The stub stands in for it.
  const component = Object.assign(ctx.vaultExportMixin(), {
    confirm: async (message, options) => {
      asked.push({ message, options });
      return overrides.__confirmAnswer !== false;
    },
  });
  return { component, downloads, asked };
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

test('the interchange export asks before anything is decrypted', async () => {
  // Cancelling must mean nothing was built, not "we decrypted it all then
  // threw it away".
  let built = 0;
  const { component, downloads, asked } = load({
    // Records AND refuses: a stub that only refused would let the assertions
    // below pass even if the confirm were never reached at all.
    __confirmAnswer: false,
    vaultExportTree: { buildTree: async () => { built += 1; return { vaults: [] }; } },
  });
  component.exportFormat = 'interchange';
  await component.runExport();
  assert.equal(asked.length, 1, 'the user was never asked');
  assert.equal(built, 0, 'the tree was built despite the refusal');
  assert.equal(downloads.length, 0);
});

test('the warning uses the option name the dialog actually reads', () => {
  // okLabel, not confirmLabel: an invented name leaves the button on "OK"
  // and nothing says so.
  const { component, asked } = load();
  component.exportFormat = 'interchange';
  return component.runExport().then(() => {
    assert.equal(asked.length, 1);
    assert.ok(asked[0].options.okLabel, 'no okLabel was passed');
    assert.equal(asked[0].options.confirmLabel, undefined);
  });
});

test('the interchange export downloads once confirmed', async () => {
  const { component, downloads } = load();
  component.exportFormat = 'interchange';
  await component.runExport();
  assert.deepStrictEqual(downloads, ['vault-export-2026-09-06.json']);
});

test('no dialog to warn through means no plaintext file', async () => {
  // The shared wrapper answers yes when dialogs.js has not loaded, which is
  // right for an action the user already asked for and wrong here: this
  // confirm is the warning itself, and one nobody could see was not accepted.
  const { component, downloads } = load({ AppDialog: undefined });
  component.exportFormat = 'interchange';
  await component.runExport();
  assert.equal(downloads.length, 0);
  assert.match(component.exportError, /could not be confirmed/i);
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
  await component.runExport();
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
  await component.runExport();
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
  await component.runExport();
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
