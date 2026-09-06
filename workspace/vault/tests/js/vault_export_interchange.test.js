const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const SCRIPT = 'workspace/vault/ui/static/vault/ui/js/vault_export_interchange.js';

function load() {
  let n = 0;
  return loadScript(SCRIPT, {
    vaultCrypto: { uuidV7: () => `uuid-${(n += 1)}` },
  });
}

function tree(overrides = {}) {
  return Object.assign({
    format: 'vault-archive', version: 1, exported_at: '2026-09-06T00:00:00Z',
    vaults: [{
      name: 'Perso', description: '', icon: null, color: null, is_favorite: false,
      folders: [{ id: 0, parent: null, name: 'Banque', position: 0 }],
      tags: [{ id: 0, name: 'perso', color: 'red' }],
      entries: [{
        type: 'login', name: 'Ma banque', notes: 'note', favorite: true,
        trashed: false, folder: 0, tags: [0],
        created_at: 'x', updated_at: 'y', last_used_at: null,
        fields: { username: 'jc', password: 's3cret', uri: 'https://b.example', totp: 'otpauth://x' },
      }],
    }],
  }, overrides);
}

test('a vault becomes a top-level folder and nests the ones below it', () => {
  const { json } = load().vaultExportInterchange.toBitwarden(tree());
  assert.equal(json.encrypted, false);
  const names = json.folders.map((f) => f.name);
  assert.ok(names.includes('Perso/Banque'), `got ${JSON.stringify(names)}`);
});

test('a slash inside a name does not forge a nesting level', () => {
  // U+2215 reads the same and is not the separator. This is the one lossy
  // transform we choose rather than suffer - do not "fix" it back to '/'.
  const t = tree();
  t.vaults[0].name = 'Perso/Pro';
  const { json } = load().vaultExportInterchange.toBitwarden(t);
  assert.ok(json.folders.some((f) => f.name === 'Perso∕Pro/Banque'));
  assert.ok(!json.folders.some((f) => f.name === 'Perso/Pro/Banque'));
});

test('an item carries its login block and points at its folder', () => {
  const { json } = load().vaultExportInterchange.toBitwarden(tree());
  const item = json.items[0];
  assert.equal(item.type, 1);
  assert.equal(item.name, 'Ma banque');
  assert.equal(item.notes, 'note');
  assert.equal(item.favorite, true);
  assert.equal(item.login.username, 'jc');
  assert.equal(item.login.password, 's3cret');
  assert.equal(item.login.totp, 'otpauth://x');
  // Objects built inside the vm carry that realm's prototypes, so they must be
  // normalized before deepStrictEqual's identity check against a host literal.
  assert.deepStrictEqual(Array.from(item.login.uris, (u) => ({ ...u })), [{ match: null, uri: 'https://b.example' }]);
  const folder = json.folders.find((f) => f.name === 'Perso/Banque');
  assert.equal(item.folderId, folder.id);
});

test('tags become custom fields, since the format has nowhere else for them', () => {
  const { json } = load().vaultExportInterchange.toBitwarden(tree());
  assert.deepStrictEqual(Array.from(json.items[0].fields, (f) => ({ ...f })), [{ name: 'tag', value: 'perso', type: 0 }]);
});

test('the trash is not in the interchange export', () => {
  // A move to a competitor, not a backup: resurrecting what the user threw
  // away is the wrong outcome, and the exporter on the other side agrees.
  const t = tree();
  t.vaults[0].entries.push(Object.assign({}, t.vaults[0].entries[0], { trashed: true }));
  const { json } = load().vaultExportInterchange.toBitwarden(t);
  assert.equal(json.items.length, 1);
});

test('a type with no counterpart is skipped and counted, never disguised', () => {
  // Zero today, because login is the only type. This is the code that keeps a
  // future type from shipping as a login.
  const t = tree();
  t.vaults[0].entries.push(Object.assign({}, t.vaults[0].entries[0], { type: 'passport' }));
  const { json, skipped } = load().vaultExportInterchange.toBitwarden(t);
  assert.equal(skipped, 1);
  assert.equal(json.items.length, 1);
});

test('no identifier from the account reaches the file', () => {
  const { json } = load().vaultExportInterchange.toBitwarden(tree());
  json.items.forEach((item) => assert.match(item.id, /^uuid-/));
  json.folders.forEach((folder) => assert.match(folder.id, /^uuid-/));
});
