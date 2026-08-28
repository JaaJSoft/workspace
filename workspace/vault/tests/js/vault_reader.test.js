// What the listing is allowed to open, and what happens to a row that does
// not verify. Both are security properties, so both are pinned here rather
// than left to the browser walk to notice.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const VAULT = { uuid: 'v-1', wrapped_key: 'AQ' };

const ROW = {
  uuid: 'e-1',
  vault: 'v-1',
  type: 'login',
  folder: null,
  tags: ['t-1'],
  is_favorite: true,
  encrypted_name: 'ct:name',
  encrypted_notes: '',
  key_version: 1,
  entry_version: 1,
  metadata_sig: 'sig',
  deleted_at: null,
  updated_at: '2026-08-27',
  created_at: '2026-07-12',
  entry_fields: [
    { field_id: 'username', encrypted_value: 'ct:username' },
    { field_id: 'password', encrypted_value: 'ct:password' },
    { field_id: 'totp', encrypted_value: 'ct:totp' },
  ],
};

function reader(overrides = {}) {
  const opened = [];
  const verified = [];
  const ctx = loadScript('workspace/vault/ui/static/vault/ui/js/vault_reader.js', {
    TextDecoder: globalThis.TextDecoder,
    TextEncoder: globalThis.TextEncoder,
    vaultCrypto: {
      fromBase64Url: (value) => value,
      // The plaintext is the associated data, so a test can see which slot a
      // value was opened under without a real cipher.
      open: async (key, ciphertext, ad) => {
        opened.push(ad);
        return new TextEncoder().encode('open:' + ad);
      },
      AD: {
        entryFieldAd: (uuid, field) => `${uuid}|${field}`,
        folderFieldAd: (uuid, field) => `folder:${uuid}|${field}`,
        tagFieldAd: (uuid, field) => `tag:${uuid}|${field}`,
      },
      entryMetadataPayload: (fields) => fields,
      folderMetadataPayload: (fields) => fields,
      tagMetadataPayload: (fields) => fields,
      ...overrides.crypto,
    },
  });
  const session = {
    accountUuid: () => 'account-1',
    openEntryKey: async () => new Uint8Array(32),
    openVaultKey: async () => new Uint8Array(32),
    verifyRecord: async (payload, sig, type) => { verified.push([payload, type]); },
    ...overrides.session,
  };
  return { ctx, session, opened, verified };
}

test('a listing opens the name and the login, and nothing else', async () => {
  // The rule the whole screen rests on: a page sitting open holds no secret
  // in component state, so a password never reaches it at load.
  const { ctx, session, opened } = reader();
  const { rows } = await ctx.vaultReader.readEntries(session, VAULT, [ROW]);
  assert.deepStrictEqual(Array.from(opened), ['e-1|name', 'e-1|username']);
  assert.equal(rows[0].name, 'open:e-1|name');
  assert.equal(rows[0].username, 'open:e-1|username');
  assert.ok(!('password' in rows[0]));
});

test('the row says which fields it carries without opening them', async () => {
  // What lets the browser tell a login holding an authenticator key from one
  // that does not, which is what the action endpoint keys its answer on.
  const { ctx, session } = reader();
  const { rows } = await ctx.vaultReader.readEntries(session, VAULT, [ROW]);
  assert.deepStrictEqual(Array.from(rows[0].fieldIds), ['username', 'password', 'totp']);
});

test('a row is verified before anything is opened', async () => {
  const order = [];
  const { ctx, session } = reader({
    session: {
      verifyRecord: async () => { order.push('verify'); },
      openEntryKey: async () => { order.push('key'); return new Uint8Array(32); },
    },
  });
  await ctx.vaultReader.readEntries(session, VAULT, [ROW]);
  assert.deepStrictEqual(Array.from(order), ['verify', 'key']);
});

test('the signed payload names the signer, not the vault owner', async () => {
  const { ctx, session, verified } = reader();
  await ctx.vaultReader.readEntries(session, VAULT, [ROW]);
  assert.equal(verified[0][0].signer_account_uuid, 'account-1');
  assert.equal(verified[0][1], 'entry-metadata');
});

test('a row whose signature fails leaves the listing and is counted', async () => {
  // Not a partial render and not a name shown "to help identify it": the
  // count is all that may be said about it.
  const { ctx, session } = reader({
    session: {
      verifyRecord: async (payload) => {
        if (payload.entry_uuid === 'e-1') throw new Error('bad signature');
      },
    },
  });
  const second = { ...ROW, uuid: 'e-2' };
  const result = await ctx.vaultReader.readEntries(session, VAULT, [ROW, second]);
  assert.equal(result.tamperedCount, 1);
  assert.deepStrictEqual(Array.from(result.rows.map((r) => r.uuid)), ['e-2']);
});

test('one unreadable row does not cost the others their listing', async () => {
  const { ctx, session } = reader({
    crypto: {
      open: async (key, ciphertext, ad) => {
        if (ad === 'e-1|name') throw new Error('cannot open');
        return new TextEncoder().encode('open:' + ad);
      },
    },
  });
  const result = await ctx.vaultReader.readEntries(session, VAULT, [ROW, { ...ROW, uuid: 'e-2' }]);
  assert.equal(result.tamperedCount, 1);
  assert.equal(result.rows.length, 1);
});

test('a lock is not tampering', async () => {
  // Reporting an idle timeout as a forged signature would tell the user to
  // distrust a vault that merely closed.
  const { ctx, session } = reader({
    session: {
      verifyRecord: async () => {
        const err = new Error('locked');
        err.reason = 'locked';
        throw err;
      },
    },
  });
  await assert.rejects(
    () => ctx.vaultReader.readEntries(session, VAULT, [ROW]),
    (err) => err.reason === 'locked',
  );
});

test('a field is opened on demand, under its own slot', async () => {
  const { ctx, session, opened } = reader();
  const value = await ctx.vaultReader.openField(session, VAULT, ROW, 'password');
  assert.equal(value, 'open:e-1|password');
  assert.deepStrictEqual(Array.from(opened), ['e-1|password']);
});

test('asking for a field the row does not carry opens nothing', async () => {
  const { ctx, session, opened } = reader();
  const value = await ctx.vaultReader.openField(session, VAULT, ROW, 'uri');
  assert.equal(value, '');
  assert.deepStrictEqual(Array.from(opened), []);
});

test('folders and tags are verified and named the same way', async () => {
  const { ctx, session, verified } = reader();
  const folders = await ctx.vaultReader.readFolders(session, VAULT, [
    { uuid: 'f-1', vault: 'v-1', parent: null, encrypted_name: 'ct', position: 0, metadata_sig: 'sig' },
  ]);
  const tags = await ctx.vaultReader.readTags(session, VAULT, [
    { uuid: 't-1', vault: 'v-1', encrypted_name: 'ct', color: '#22c55e', metadata_sig: 'sig' },
  ]);
  assert.equal(folders.rows[0].name, 'open:folder:f-1|name');
  assert.equal(tags.rows[0].name, 'open:tag:t-1|name');
  assert.equal(tags.rows[0].color, '#22c55e');
  assert.deepStrictEqual(
    Array.from(verified.map((entry) => entry[1])),
    ['folder-metadata', 'tag-metadata'],
  );
});
