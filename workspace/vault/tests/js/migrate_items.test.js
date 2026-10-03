// Rewriting a verified row under the current suite. Format-1 inputs are built
// inside the vm (the bundle no longer writes them) with a hand-rolled sealer
// that follows the wire layout the shared vectors pin.
const test = require('node:test');
const assert = require('node:assert');
const vm = require('node:vm');
const { loadScript } = require('../../../common/tests/js/loader');

const BUNDLE = 'workspace/vault/ui/static/vault/ui/js/vendor/vault-crypto.js';
const JS = 'workspace/vault/ui/static/vault/ui/js/';
const KEY_B64 = 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8';
const ENTRY = '0192f3a4-5b6c-7d8e-9f01-23456789abcd';
const VAULT_UUID = '0192f3a4-2222-7d8e-9f01-23456789abcd';
const FOLDER = '0192f3a4-3333-7d8e-9f01-23456789abcd';
const TAG = '0192f3a4-4444-7d8e-9f01-23456789abcd';
const ACCOUNT = '0192f3a4-1111-7d8e-9f01-23456789abcd';
const SIG = 'AXNpZw';

// A deterministic nonce makes a reseal and a fresh write byte-identical.
const fixedRandom = {
  subtle: globalThis.crypto.subtle,
  getRandomValues(bytes) { bytes.fill(7); return bytes; },
};

function setup() {
  const ctx = loadScript(BUNDLE, {
    crypto: fixedRandom,
    TextEncoder: globalThis.TextEncoder,
    TextDecoder: globalThis.TextDecoder,
    btoa: globalThis.btoa,
    atob: globalThis.atob,
  });
  for (const file of ['entry_write', 'folder_write', 'tag_write', 'vault_update', 'migrate_items']) {
    loadScript(JS + file + '.js', {}, ctx);
  }
  vm.runInContext(`
    var fmt1 = async function (rawB64, adText, text, headerKeyVersion) {
      const V = vaultCrypto;
      const key = await crypto.subtle.importKey('raw', V.fromBase64Url(rawB64), 'AES-GCM', false, ['encrypt']);
      const iv = new Uint8Array(12).fill(9);
      const header = Uint8Array.of(1, 1, 1, headerKeyVersion >> 8, headerKeyVersion & 255, 12);
      const body = new Uint8Array(await crypto.subtle.encrypt(
        { name: 'AES-GCM', iv, additionalData: new TextEncoder().encode('v1|' + adText) },
        key, new TextEncoder().encode(text)));
      const out = new Uint8Array(header.length + iv.length + body.length);
      out.set(header, 0); out.set(iv, header.length); out.set(body, header.length + iv.length);
      return V.toBase64Url(out);
    };
  `, ctx);
  return { ctx, V: ctx.vaultCrypto, signed: [] };
}

function sessionFor(V, signed) {
  const key = () => V.importAeadKey(V.fromBase64Url(KEY_B64));
  return {
    accountUuid: () => ACCOUNT,
    openEntryKey: key,
    openVaultKey: key,
    sign: async (payload) => { signed.push(payload); return 'AQ'; },
  };
}

const plain = (V, key, b64, context) =>
  V.open(key, V.fromBase64Url(b64), context).then((b) => new TextDecoder().decode(b));
const plainJson = (value) => JSON.parse(JSON.stringify(value));

test('a current ciphertext is carried untouched', async () => {
  const { ctx, V } = setup();
  const key = await V.importAeadKey(V.fromBase64Url(KEY_B64));
  const context = V.AD.entryFieldAd(ENTRY, 'password');
  const current = V.toBase64Url(
    await V.seal(key, new Uint8Array([1]), context, { keyVersion: 1, kdfId: V.KDF_HKDF_SHA256 }),
  );
  assert.equal(await ctx.vaultReseal(V, key, current, context, 1), current);
});

test('a format-1 ciphertext becomes format 2 under the given key version and keeps its bytes', async () => {
  const { ctx, V } = setup();
  const key = await V.importAeadKey(V.fromBase64Url(KEY_B64));
  const context = V.AD.entryFieldAd(ENTRY, 'password');
  const old = await ctx.fmt1(KEY_B64, `entry-field|${ENTRY}|password`, 'hunter2', 1);
  assert.notEqual(V.decodeCiphertext(V.fromBase64Url(old)).keyVersion, 3);
  const out = await ctx.vaultReseal(V, key, old, context, 3);
  const decoded = V.decodeCiphertext(V.fromBase64Url(out));
  assert.equal(decoded.formatVersion, 2);
  assert.equal(decoded.keyVersion, 3);
  assert.equal(decoded.kdfId, V.KDF_HKDF_SHA256);
  assert.equal(await plain(V, key, out, context), 'hunter2');
});

async function entryRow(ctx, { notes = '', fields = {}, favorite = false, folder = null, tags = [] } = {}) {
  const sealed = (id, text) => ctx.fmt1(KEY_B64, `entry-field|${ENTRY}|${id}`, text, 1);
  return {
    uuid: ENTRY, type: 'login', folder, tags, is_favorite: favorite,
    key_version: 3, entry_version: 2,
    encrypted_name: await sealed('name', 'GitHub'),
    encrypted_notes: notes ? await sealed('notes', notes) : '',
    entry_fields: await Promise.all(
      Object.entries(fields).map(async ([id, text]) => ({ field_id: id, encrypted_value: await sealed(id, text) })),
    ),
    metadata_sig: SIG,
  };
}

test('an entry item has exactly the key set the endpoint accepts', async () => {
  const { ctx, V, signed } = setup();
  const row = await entryRow(ctx);
  const item = await ctx.buildEntryMigrateItem(sessionFor(V, signed), { uuid: VAULT_UUID, key_version: 3 }, row);
  assert.deepStrictEqual(
    Array.from(Object.keys(item)).sort(),
    ['encrypted_name', 'encrypted_notes', 'expected_sig', 'fields', 'kind', 'metadata_sig', 'uuid'],
  );
  assert.equal(item.encrypted_notes, '');
  assert.equal(Object.keys(item.fields).length, 0);
  assert.equal(item.expected_sig, SIG);
  assert.equal(V.decodeCiphertext(V.fromBase64Url(item.encrypted_name)).formatVersion, 2);
});

test('an entry signs what a fresh write of the same record signs', async () => {
  const { ctx, V, signed } = setup();
  const session = sessionFor(V, signed);
  const vault = { uuid: VAULT_UUID, key_version: 3 };
  const row = await entryRow(ctx, {
    notes: 'n', fields: { password: 'hunter2', username: 'jc' }, favorite: true, folder: FOLDER, tags: [TAG, ACCOUNT],
  });
  const item = await ctx.buildEntryMigrateItem(session, vault, row);
  const migrated = signed.pop();
  const written = await ctx.buildEntryWriteRequest(session, vault, {
    uuid: ENTRY, type: 'login', folder: FOLDER, tags: [TAG, ACCOUNT], favorite: true,
    entryVersion: 2, name: 'GitHub', notes: 'n', values: { password: 'hunter2', username: 'jc' },
  });
  const expected = signed.pop();
  assert.deepStrictEqual(plainJson(migrated), plainJson(expected));
  assert.equal(item.encrypted_name, written.encrypted_name);
  assert.equal(item.encrypted_notes, written.encrypted_notes);
  assert.deepStrictEqual(plainJson(item.fields), plainJson(written.fields));
  assert.equal(migrated.key_version, 3);
});

test('a folder signs what a fresh write signs, under the vault key version', async () => {
  const { ctx, V, signed } = setup();
  const session = sessionFor(V, signed);
  const vault = { uuid: VAULT_UUID, key_version: 2 };
  const row = {
    uuid: FOLDER, parent: ENTRY, position: 4, metadata_sig: SIG,
    encrypted_name: await ctx.fmt1(KEY_B64, `folder-field|${FOLDER}|name`, 'Work', 1),
  };
  const item = await ctx.buildFolderMigrateItem(session, vault, row);
  const written = await ctx.buildFolderWriteRequest(session, vault, { uuid: FOLDER, parent: ENTRY, position: 4, name: 'Work' });
  assert.deepStrictEqual(
    Array.from(Object.keys(item)).sort(), ['encrypted_name', 'expected_sig', 'kind', 'metadata_sig', 'uuid'],
  );
  assert.equal(V.decodeCiphertext(V.fromBase64Url(item.encrypted_name)).keyVersion, 2);
  assert.equal(item.encrypted_name, written.encrypted_name);
  assert.deepStrictEqual(plainJson(signed[0]), plainJson(signed[1]));
});

test('a tag signs what a fresh write signs', async () => {
  const { ctx, V, signed } = setup();
  const session = sessionFor(V, signed);
  const vault = { uuid: VAULT_UUID, key_version: 2 };
  const row = {
    uuid: TAG, color: '#ff0000', metadata_sig: SIG,
    encrypted_name: await ctx.fmt1(KEY_B64, `tag-field|${TAG}|name`, 'Urgent', 1),
  };
  const item = await ctx.buildTagMigrateItem(session, vault, row);
  const written = await ctx.buildTagWriteRequest(session, vault, { uuid: TAG, color: '#ff0000', name: 'Urgent' });
  assert.deepStrictEqual(
    Array.from(Object.keys(item)).sort(), ['encrypted_name', 'expected_sig', 'kind', 'metadata_sig', 'uuid'],
  );
  assert.equal(item.encrypted_name, written.encrypted_name);
  assert.deepStrictEqual(plainJson(signed[0]), plainJson(signed[1]));
});

async function vaultRow(ctx, description) {
  return {
    uuid: VAULT_UUID, icon: 'lock', color: 'primary', key_version: 3, is_favorite: true,
    metadata_sig: SIG,
    encrypted_name: await ctx.fmt1(KEY_B64, `vault-field|${VAULT_UUID}|name`, 'Personal', 1),
    encrypted_description: description
      ? await ctx.fmt1(KEY_B64, `vault-field|${VAULT_UUID}|description`, description, 1)
      : '',
  };
}

test('vault metadata signs what a fresh write signs and its key set is exact', async () => {
  const { ctx, V, signed } = setup();
  const session = sessionFor(V, signed);
  const vault = await vaultRow(ctx, 'About');
  const item = await ctx.buildVaultMetadataMigrateItem(session, vault);
  const written = await ctx.buildVaultUpdateRequest(
    session, { ...vault, name: 'Personal' }, { description: 'About' },
  );
  assert.deepStrictEqual(
    Array.from(Object.keys(item)).sort(),
    ['encrypted_description', 'encrypted_name', 'expected_sig', 'kind', 'metadata_sig'],
  );
  assert.equal(item.encrypted_name, written.encrypted_name);
  assert.equal(item.encrypted_description, written.encrypted_description);
  assert.deepStrictEqual(plainJson(signed[0]), plainJson(signed[1]));
  assert.equal(V.decodeCiphertext(V.fromBase64Url(item.encrypted_name)).keyVersion, 3);
});

test('a vault with no description keeps an empty one', async () => {
  const { ctx, V, signed } = setup();
  const item = await ctx.buildVaultMetadataMigrateItem(sessionFor(V, signed), await vaultRow(ctx, ''));
  assert.equal(item.encrypted_description, '');
});
