// What cbor-x leaves behind after an encode.
//
// This is the one place the module produces a single contiguous plaintext of
// the whole account: an export encodes the entire tree in one call, and
// buildArchive wipes what canonicalCbor hands it precisely because of that.
// cbor-x, though, encodes into a buffer it owns and reuses between calls and
// returns a *view* into it, so wiping the caller's copy leaves the original
// resident - after the export, after the vault locks, for as long as the tab
// lives. Nothing downstream can reach that buffer, so it has to be wiped where
// it is produced, and this is what checks that it was.
//
// It runs against the bundle SOURCE, not the built artifact: `target` is a
// module-scoped binding inside cbor-x, shared by every Encoder in the realm,
// and the bundle closes over it where no test can see it. A second encoder
// here writes into that same buffer, which is what makes the leak observable.
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const fs = require('node:fs');
const { pathToFileURL } = require('node:url');

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..', '..');
const FRONTEND = path.join(REPO_ROOT, 'scripts', 'frontend');
const CBOR_SOURCE = path.join(FRONTEND, 'src', 'vault', 'cbor.js');
// The specifier `cbor-x` resolves to this under Node's "import" condition.
// Reached by path so the test lands on the very module instance cbor.js
// imported - a second instance would carry a second buffer and see nothing.
const CBOR_X = path.join(FRONTEND, 'node_modules', 'cbor-x', 'node-index.js');

const CANARY = 'CANARY-PLAINTEXT-CANARY-PLAINTEXT';

test('an encoded payload is not left behind in the buffer cbor-x reuses', async (t) => {
  if (!fs.existsSync(CBOR_X)) {
    // `npm ci` in scripts/frontend is what puts it there; the CI job that runs
    // this file does it. Skipping rather than failing keeps a checkout with no
    // frontend dependencies usable for every other suite.
    t.skip('scripts/frontend dependencies are not installed');
    return;
  }
  const { canonicalCbor } = await import(pathToFileURL(CBOR_SOURCE).href);
  const { Encoder } = await import(pathToFileURL(CBOR_X).href);

  canonicalCbor({ v: 1, secret: CANARY });

  // Any encoder in this realm writes into the same module-scoped buffer, so
  // the view it returns exposes that buffer whole.
  const probe = new Encoder({ useRecords: false, mapsAsObjects: false });
  const shared = Buffer.from(new Uint8Array(probe.encode({ v: 1 }).buffer));

  assert.equal(
    shared.toString('latin1').includes(CANARY), false,
    'the plaintext stayed in the buffer cbor-x keeps between calls'
  );
});

test('the probe would see the plaintext if it were left there', async (t) => {
  // Guards the test above, which asserts an absence: a probe that could never
  // find the canary would pass whatever canonicalCbor did. Encoding straight
  // through cbor-x - no wipe anywhere - has to leave it visible.
  if (!fs.existsSync(CBOR_X)) {
    t.skip('scripts/frontend dependencies are not installed');
    return;
  }
  const { Encoder } = await import(pathToFileURL(CBOR_X).href);
  const encoder = new Encoder({ useRecords: false, mapsAsObjects: false });
  encoder.encode({ v: 1, secret: CANARY });

  const probe = new Encoder({ useRecords: false, mapsAsObjects: false });
  const shared = Buffer.from(new Uint8Array(probe.encode({ v: 1 }).buffer));

  assert.equal(
    shared.toString('latin1').includes(CANARY), true,
    'the probe cannot see a plaintext even when nothing wiped it - it proves nothing'
  );
});

// The archive's encoding. Sealed rather than signed, so it must store exactly
// what it was given, and it must stay inside the arena the caller wipes.
async function archiveEncoding(t) {
  if (!fs.existsSync(CBOR_X)) {
    t.skip('scripts/frontend dependencies are not installed');
    return null;
  }
  const cbor = await import(pathToFileURL(CBOR_SOURCE).href);
  const cborX = await import(pathToFileURL(CBOR_X).href);
  // A Buffer here and a Uint8Array in the browser: under Node, cbor-x writes
  // strings of 64 units and more through Buffer#utf8Write, which a bare
  // Uint8Array does not have. The browser build writes through
  // TextEncoder#encodeInto, which takes the Uint8Array the archive allocates.
  const arenaFor = (payload) => Buffer.alloc(cbor.cborSizeBound(payload));
  return { ...cbor, cborX, arenaFor };
}

test('an archive payload keeps its strings exactly as they were given', async (t) => {
  // A password typed in NFD that came back in NFC would be a different
  // credential, and two custom field ids that fold to one under NFC would
  // refuse the export outright.
  const encoding = await archiveEncoding(t);
  if (!encoding) return;
  const nfdPassword = 'café-s3cret';
  const payload = { password: nfdPassword, fields: { 'custom:é': 'nfc', 'custom:é': 'nfd' } };
  const decoded = encoding.cborX.decode(encoding.encodeCbor(payload, encoding.arenaFor(payload)));
  assert.equal(decoded.password, nfdPassword);
  assert.equal(decoded.fields['custom:é'], 'nfc');
  assert.equal(decoded.fields['custom:é'], 'nfd');
});

test('an archive larger than cbor-x starts with is encoded without growing a buffer', async (t) => {
  // Growing allocates a larger buffer, copies into it and abandons the old one
  // with a prefix of the plaintext still in it. The arena coming back is the
  // proof nothing grew: a grown encoding is a view into some other buffer.
  const encoding = await archiveEncoding(t);
  if (!encoding) return;
  const entries = Array.from({ length: 2000 }, (_, i) => ({
    name: `entry ${i} éè中🔑`,
    fields: { username: `user${i}@example.com`, password: `${CANARY}-${i}-ééé` },
    // Past 64 units, the length at which cbor-x switches to its bulk UTF-8
    // writer - and reserves three bytes per unit for it.
    notes: `${'é'.repeat(80)} ${i} ${'🔑'.repeat(40)}`,
  }));
  const payload = { vaults: [{ name: 'Perso', entries }] };
  const arena = encoding.arenaFor(payload);
  const view = encoding.encodeCbor(payload, arena);
  assert.ok(view.length > 64 * 1024, `the payload is only ${view.length} bytes`);
  assert.equal(view.buffer, arena.buffer, 'the encoding left the arena');
  assert.equal(encoding.cborX.decode(view).vaults[0].entries.length, entries.length);
});

test('cbor-x is handed back a buffer of its own once an archive is encoded', async (t) => {
  // Its buffer is shared by every encoder in the realm. Left on the arena,
  // the next signed payload would be written into the caller's memory, and
  // the arena would outlive the export.
  const encoding = await archiveEncoding(t);
  if (!encoding) return;
  const payload = { secret: CANARY };
  const arena = encoding.arenaFor(payload);
  encoding.encodeCbor(payload, arena);
  const probe = new encoding.cborX.Encoder({ useRecords: false, mapsAsObjects: false });
  const shared = probe.encode({ v: 1 });
  assert.notEqual(shared.buffer, arena.buffer, 'cbor-x still writes into the arena');
  assert.equal(
    Buffer.from(new Uint8Array(shared.buffer)).toString('latin1').includes(CANARY), false,
    'the plaintext is readable through the buffer cbor-x kept'
  );
});

test('a long plain-ASCII value does not outgrow its arena either', async (t) => {
  // It encodes to one byte per unit, but cbor-x reserves three before it
  // writes. In a payload made mostly of such a value, an arena sized to the
  // final bytes is outgrown by that reservation alone.
  const encoding = await archiveEncoding(t);
  if (!encoding) return;
  const payload = { notes: 'x'.repeat(100000) };
  const arena = encoding.arenaFor(payload);
  const view = encoding.encodeCbor(payload, arena);
  assert.equal(view.buffer, arena.buffer, 'the encoding left the arena');
});
