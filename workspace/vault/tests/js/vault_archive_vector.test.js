// The vector the Python reference opens. Two implementations, no shared line:
// the browser has its own cbor.js, the reference has cbor2.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { loadScripts } = require('../../../common/tests/js/loader');

const VECTOR = path.join(__dirname, '..', 'fixtures', 'archive_vector.json');
const PASSPHRASE = 'correcte cheval batterie agrafe sept huit neuf';
const TREE_JSON = JSON.stringify({
  format: 'vault-archive',
  version: 1,
  exported_at: '2026-09-06T00:00:00Z',
  vaults: [{
    name: 'Perso', description: 'Le coffre personnel', icon: null, color: null,
    is_favorite: true,
    folders: [{ id: 0, parent: null, name: 'Banque', position: 0 }],
    tags: [{ id: 0, name: 'perso', color: 'red' }],
    entries: [{
      type: 'login', name: 'Ma banque', notes: 'deux lignes\nde notes',
      favorite: true, trashed: false, folder: 0, tags: [0],
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T00:00:00Z',
      last_used_at: null,
      fields: { username: 'jc', password: 's3cret', uri: 'https://b.example' },
    }],
  }],
});

test('writes the archive vector the reference implementation opens', async () => {
  // vaultCrypto is the REAL bundle here, never a stub: the vector is worthless
  // without the real Argon2 and the real CBOR encoder. It is reached the way
  // crypto_parity.test.js reaches it - the vendor bundle run through the shared
  // loader with the browser globals it needs - and the writer is run in that
  // same context, the way base.html loads the two together. One context is not
  // a convenience: it is what keeps the tree, the encoder and the writer in a
  // single realm.
  const ctx = loadScripts(
    [
      'workspace/vault/ui/static/vault/ui/js/vendor/vault-crypto.js',
      'workspace/vault/ui/static/vault/ui/js/vault_archive.js',
    ],
    {
      crypto: globalThis.crypto,
      TextEncoder: globalThis.TextEncoder,
      TextDecoder: globalThis.TextDecoder,
      btoa: globalThis.btoa,
      atob: globalThis.atob,
      __treeJson: TREE_JSON,
    }
  );
  // Built inside the vm: an object created out here carries this realm's
  // prototypes, and the encoder branches on them - the cbor-x incident.
  const tree = vm.runInContext('JSON.parse(__treeJson)', ctx);
  // Salt and nonce are pinned so the vector is reproducible; every other
  // export draws both.
  const bytes = await ctx.vaultArchive.buildArchive({
    tree: tree,
    passphrase: PASSPHRASE,
    salt: new Uint8Array(32).fill(0x2a),
    iv: new Uint8Array(12).fill(0x0c),
  });
  fs.writeFileSync(VECTOR, `${JSON.stringify({
    passphrase: PASSPHRASE,
    tree: JSON.parse(TREE_JSON),
    archive_hex: Buffer.from(bytes).toString('hex'),
  }, null, 2)}\n`);
  assert.ok(bytes.length > 50);
});
