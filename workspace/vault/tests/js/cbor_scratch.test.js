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
