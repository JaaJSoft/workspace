// The vectors the Python reference opens. Two implementations, no shared line:
// the browser has its own cbor.js, the reference has cbor2.
//
// Two of them, and the second one is not redundant. The container publishes m,
// t and p in the clear so an archive is read with the cost it was written at;
// a vector written at the defaults cannot tell a reader that honours the
// header from one that ignores it and derives at today's constants. The
// low-cost vector is the one that can.
//
// This is a golden-file test: it rebuilds each archive and compares it to the
// committed fixture rather than rewriting it, so CI leaves the tree clean and
// the bytes Python opens are the bytes this bundle produces today. To refresh
// the fixtures after a deliberate format or fixture change:
//
//   VAULT_ARCHIVE_VECTOR_REFRESH=1 node --test workspace/vault/tests/js/vault_archive_vector.test.js
//
// then re-run the Python round trip - regenerating without re-running it is
// how a stale vector turns into a test of itself.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { loadScripts } = require('../../../common/tests/js/loader');

const FIXTURES = path.join(__dirname, '..', 'fixtures');
const REFRESH = process.env.VAULT_ARCHIVE_VECTOR_REFRESH === '1';
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

// Every value here is inside the container's bounds and none of them is the
// default: m is the floor of the allowed range - cheap enough that the Python
// round trip stays fast - and t and p differ too, so a reader that freezes any
// one of the three derives the wrong key.
const LOW_COST = { v: '1.3', m: 8192, t: 2, p: 1 };

async function buildVector(params) {
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
  // Salt and nonce are pinned so the archive is reproducible; every other
  // export draws both.
  const bytes = await ctx.vaultArchive.buildArchive({
    tree: tree,
    passphrase: PASSPHRASE,
    salt: new Uint8Array(32).fill(0x2a),
    iv: new Uint8Array(12).fill(0x0c),
    params: params ? vm.runInContext(`(${JSON.stringify(params)})`, ctx) : undefined,
  });
  assert.ok(bytes.length > 50);
  const declared = params || ctx.vaultCrypto.ARGON2_PARAMS;
  return `${JSON.stringify({
    passphrase: PASSPHRASE,
    params: { m: declared.m, t: declared.t, p: declared.p },
    tree: JSON.parse(TREE_JSON),
    archive_hex: Buffer.from(bytes).toString('hex'),
  }, null, 2)}\n`;
}

function checkOrRefresh(file, vector) {
  const target = path.join(FIXTURES, file);
  if (REFRESH) {
    fs.writeFileSync(target, vector);
    return;
  }
  assert.equal(
    fs.readFileSync(target, 'utf8').replace(/\r\n/g, '\n'),
    vector,
    `the committed ${file} no longer matches what this bundle writes. If the `
    + 'format or the fixture changed on purpose, refresh it with '
    + 'VAULT_ARCHIVE_VECTOR_REFRESH=1 and re-run the Python round trip'
  );
}

test('the committed vector is what this bundle writes today', async () => {
  checkOrRefresh('archive_vector.json', await buildVector(null));
});

test('the low-cost vector is what this bundle writes at other parameters', async () => {
  checkOrRefresh('archive_vector_low_cost.json', await buildVector(LOW_COST));
});

test('the low-cost vector is not the default cost in disguise', async () => {
  // The whole worth of the second vector is that its parameters differ from
  // the ones a reader would freeze. If a default ever moves onto one of these
  // values, this fails here rather than quietly turning the Python test that
  // opens it back into a duplicate of the first.
  const ctx = loadScripts(
    ['workspace/vault/ui/static/vault/ui/js/vendor/vault-crypto.js'],
    { crypto: globalThis.crypto, TextEncoder: globalThis.TextEncoder, TextDecoder: globalThis.TextDecoder }
  );
  const defaults = ctx.vaultCrypto.ARGON2_PARAMS;
  for (const name of ['m', 't', 'p']) {
    assert.notEqual(LOW_COST[name], defaults[name], `low-cost ${name} equals the default`);
  }
  ctx.vaultCrypto.assertArchiveParams(vm.runInContext(`(${JSON.stringify(LOW_COST)})`, ctx));
});
