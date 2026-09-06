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

// Records every id the generator hands out, in order, so a test can compare
// it against what actually ended up in the file - not just pattern-match the
// shape of an id.
function loadCounting() {
  const generated = [];
  const ctx = loadScript(SCRIPT, {
    vaultCrypto: { uuidV7: () => {
      const id = `uuid-${generated.length + 1}`;
      generated.push(id);
      return id;
    } },
  });
  return { ctx, generated };
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

test('every id in the file was drawn from the generator, and only from it', () => {
  const { ctx, generated } = loadCounting();
  const { json } = ctx.vaultExportInterchange.toBitwarden(tree());
  const emitted = json.folders.map((f) => f.id).concat(json.items.map((i) => i.id));
  assert.deepStrictEqual(Array.from(emitted), generated);
});

test('a circular parent chain does not hang the export', () => {
  const t = tree();
  t.vaults[0].folders = [
    { id: 0, parent: 1, name: 'A', position: 0 },
    { id: 1, parent: 0, name: 'B', position: 1 },
  ];
  const { json } = load().vaultExportInterchange.toBitwarden(t);
  assert.ok(json.folders.length >= 2);
});

test('a folder nested under another folder chains vault, parent and child into one path', () => {
  const t = tree();
  t.vaults[0].folders = [
    { id: 0, parent: null, name: 'Banque', position: 0 },
    { id: 1, parent: 0, name: 'Crédit', position: 0 },
  ];
  const { json } = load().vaultExportInterchange.toBitwarden(t);
  const names = json.folders.map((f) => f.name);
  assert.ok(names.includes('Perso/Banque/Crédit'), `got ${JSON.stringify(names)}`);
});

test('a slash inside a nested folder name is substituted too, not only at the top level', () => {
  const t = tree();
  t.vaults[0].folders = [
    { id: 0, parent: null, name: 'Banque', position: 0 },
    { id: 1, parent: 0, name: 'Crédit/Débit', position: 0 },
  ];
  const { json } = load().vaultExportInterchange.toBitwarden(t);
  const names = json.folders.map((f) => f.name);
  assert.ok(names.includes('Perso/Banque/Crédit∕Débit'), `got ${JSON.stringify(names)}`);
  assert.ok(!names.some((n) => n.includes('Crédit/Débit')));
});

test('an entry filed nowhere lands on the vault-level folder', () => {
  const t = tree();
  t.vaults[0].entries[0].folder = null;
  const { json } = load().vaultExportInterchange.toBitwarden(t);
  const vaultFolder = json.folders.find((f) => f.name === 'Perso');
  assert.equal(json.items[0].folderId, vaultFolder.id);
});

test('interchangeFilename names the file after the given date', () => {
  const { ctx } = loadCounting();
  const name = ctx.vaultExportInterchange.interchangeFilename(new Date('2026-09-06T12:34:56Z'));
  assert.equal(name, 'vault-export-2026-09-06.json');
});
