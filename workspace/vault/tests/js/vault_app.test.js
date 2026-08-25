// The unlock screen as a state machine. The session is stubbed - what it does
// is tested in session.test.js - so what is under test here is what the user
// sees at each step and what the screen refuses to do.
const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

function app(session = {}, api = {}, crypto = {}) {
  const ctx = loadScripts([
    'workspace/vault/ui/static/vault/ui/js/vault_create.js',
    'workspace/vault/ui/static/vault/ui/js/vault_app.js',
  ], {
    VaultSession: {
      isUnlocked: () => false,
      unlock: async () => {},
      lock() {},
      onLock() {},
      onTick() {},
      watchForIdle() {},
      secondsUntilLock: () => 300,
      rememberedSecret: () => null,
      forgetDevice() {},
      openVaultKey: async () => new Uint8Array(32),
      verifyVaultMetadata: async () => {},
      sign: async () => 'signature',
      accountUuid: () => 'account-uuid',
      accountKexPublicRaw: () => new Uint8Array(32),
      ...session,
    },
    VaultApi: { listVaults: async () => [], createVault: async () => ({}), ...api },
    VaultCrypto: {
      uuidV7: () => 'vault-uuid',
      randomBytes: () => new Uint8Array(32),
      toBase64Url: () => 'b64',
      fromBase64Url: () => new Uint8Array(1),
      seal: async () => new Uint8Array(4),
      open: async () => new TextEncoder().encode('Personal'),
      hkdf: async () => new Uint8Array(32),
      hpkeSeal: async () => new Uint8Array(64),
      decodePublicKey: () => new Uint8Array(32),
      canonicalCbor: () => new Uint8Array(2),
      KDF_HKDF_SHA256: 0x01,
      HPKE_SUITE_V1: { kem_id: 32, kdf_id: 1, aead_id: 2, mode: 0 },
      AD: {
        vaultFieldAd: () => 'ad',
        vaultKeyInfo: () => 'info',
        vaultMetaInfo: () => 'meta-info',
      },
      vaultMetadataPayload: (fields) => fields,
      ...crypto,
    },
    TextEncoder: globalThis.TextEncoder,
    TextDecoder: globalThis.TextDecoder,
    document: { addEventListener() {} },
    addEventListener() {},
  });
  return ctx.vaultApp();
}

test('the screen starts locked', () => {
  assert.equal(app().state, 'locked');
});

test('a device with no remembered key asks for one', () => {
  assert.equal(app().needsSecret(), true);
});

test('a remembered key is used without asking for it again', () => {
  const component = app({ rememberedSecret: () => 'A'.repeat(53) });
  component.init();
  assert.equal(component.needsSecret(), false);
  assert.equal(component.secretText, 'A'.repeat(53));
});

test('the deriving state is entered before the wait, not after', async () => {
  const seen = [];
  const component = app({
    unlock: async () => { seen.push(component.state); },
  });
  await component.unlock();
  assert.deepEqual(seen, ['deriving']);
});

test('a wrong password says so and returns to the entry state', async () => {
  const component = app({
    unlock: async () => { const e = new Error('x'); e.reason = 'password'; throw e; },
  });
  await component.unlock();
  assert.equal(component.state, 'locked');
  assert.match(component.error, /password/i);
});

test('a substituted key is not reported as a wrong password', async () => {
  const component = app({
    unlock: async () => { const e = new Error('x'); e.reason = 'substituted-key'; throw e; },
  });
  await component.unlock();
  assert.match(component.error, /key the server returned|does not match/i);
});

test('a corrupt remembered recovery key can be retyped, not just stared at', async () => {
  let forgotten = false;
  const component = app({
    unlock: async () => { const e = new Error('x'); e.reason = 'recovery-key'; throw e; },
    forgetDevice: () => { forgotten = true; },
  });
  component.secretText = 'A'.repeat(53);
  assert.equal(component.needsSecret(), false);
  await component.unlock();
  assert.equal(component.secretText, '');
  assert.equal(component.needsSecret(), true);
  assert.equal(forgotten, true);
});

test('a wrong password does not clear the remembered recovery key', async () => {
  let forgotten = false;
  const component = app({
    unlock: async () => { const e = new Error('x'); e.reason = 'password'; throw e; },
    forgetDevice: () => { forgotten = true; },
  });
  component.secretText = 'A'.repeat(53);
  await component.unlock();
  assert.equal(component.secretText, 'A'.repeat(53));
  assert.equal(forgotten, false);
});

test('the password is dropped from the component whatever the outcome', async () => {
  const component = app({
    unlock: async () => { const e = new Error('x'); e.reason = 'password'; throw e; },
  });
  component.password = 'secret';
  await component.unlock();
  assert.equal(component.password, '');
});

test('a successful unlock loads the vault list', async () => {
  let listed = 0;
  const component = app(
    { isUnlocked: () => true },
    { listVaults: async () => { listed += 1; return []; } }
  );
  await component.unlock();
  assert.equal(component.state, 'unlocked');
  assert.equal(listed, 1);
});

test('locking clears the decrypted names from the component', async () => {
  let onLock = null;
  const component = app({
    isUnlocked: () => true,
    onLock: (fn) => { onLock = fn; },
    verifyVaultMetadata: async () => {},
  }, {
    listVaults: async () => [
      { uuid: 'v1', encrypted_name: 'AQ', wrapped_key: 'AQ', metadata_sig: 'AQ',
        owner_account_uuid: 'account-uuid', encrypted_description: '', icon: 'lock',
        color: 'primary', key_version: 1, is_favorite: false },
    ],
  });
  component.init();
  await component.unlock();
  assert.equal(component.vaults.length, 1);
  onLock();
  assert.deepEqual(component.vaults, []);
  assert.equal(component.state, 'locked');
});

test('a vault whose signature does not verify is flagged, not shown as normal', async () => {
  const component = app({
    isUnlocked: () => true,
    verifyVaultMetadata: async () => { throw new Error('bad signature'); },
  }, {
    listVaults: async () => [
      { uuid: 'v1', encrypted_name: 'AQ', wrapped_key: 'AQ', metadata_sig: 'AQ',
        owner_account_uuid: 'account-uuid', encrypted_description: '', icon: 'lock',
        color: 'primary', key_version: 1, is_favorite: false },
    ],
  });
  await component.unlock();
  assert.equal(component.vaults[0].tampered, true);
  assert.equal(component.vaults[0].name, '');
});

test('a vault with no key wrap is reported rather than silently missing', async () => {
  const component = app({ isUnlocked: () => true }, {
    listVaults: async () => [
      { uuid: 'v1', encrypted_name: 'AQ', wrapped_key: null, metadata_sig: 'AQ',
        owner_account_uuid: 'account-uuid', encrypted_description: '', icon: 'lock',
        color: 'primary', key_version: 1, is_favorite: false },
    ],
  });
  await component.unlock();
  assert.equal(component.vaults[0].unopenable, true);
});

test('a vault whose name cannot be decrypted is flagged without hiding the rest of the list', async () => {
  const rowOf = (uuid) => ({
    uuid, encrypted_name: 'AQ', wrapped_key: 'AQ', metadata_sig: 'AQ',
    owner_account_uuid: 'account-uuid', encrypted_description: '', icon: 'lock',
    color: 'primary', key_version: 1, is_favorite: false,
  });
  const component = app({ isUnlocked: () => true }, {
    listVaults: async () => [rowOf('v1'), rowOf('v2')],
  }, {
    open: async (key, raw, ad) => {
      if (String(ad).includes('v1')) throw new Error('tag mismatch');
      return new TextEncoder().encode('Work');
    },
    AD: {
      vaultFieldAd: (uuid, field) => `${uuid}:${field}`,
      vaultKeyInfo: () => 'info',
      vaultMetaInfo: () => 'meta-info',
    },
  });
  await component.unlock();
  assert.equal(component.vaults.length, 2);
  assert.equal(component.vaults[0].unreadable, true);
  assert.equal(component.vaults[0].name, '');
  assert.equal(component.vaults[1].name, 'Work');
});

test('a listing failure after a successful unlock keeps the session and the screen agreeing', async () => {
  const component = app({ isUnlocked: () => true }, {
    listVaults: async () => { throw new Error('network down'); },
  });
  await component.unlock();
  // VaultSession reports isUnlocked() === true in this harness: the screen
  // must say the same thing, not fall back to the password form while the
  // session still holds live keys.
  assert.equal(component.state, 'unlocked');
  assert.ok(component.error);
});

test('the signed payload carries vault_uuid, not the row\'s own uuid key', async () => {
  let captured = null;
  const component = app({ isUnlocked: () => true }, {
    listVaults: async () => [
      { uuid: 'v1', encrypted_name: 'AQ', wrapped_key: 'AQ', metadata_sig: 'AQ',
        owner_account_uuid: 'account-uuid', encrypted_description: '', icon: 'lock',
        color: 'primary', key_version: 1, is_favorite: false },
    ],
  }, {
    vaultMetadataPayload: (fields) => { captured = fields; return fields; },
  });
  await component.unlock();
  assert.equal(captured.vault_uuid, 'v1');
});

test('the countdown is rendered as minutes and seconds', () => {
  const a = app();
  a.secondsLeft = 272;
  assert.equal(a.countdown(), '4:32');
  const b = app();
  b.secondsLeft = 9;
  assert.equal(b.countdown(), '0:09');
});

test('init subscribes to the session ticker so the countdown keeps updating', () => {
  let onTickCallback = null;
  const component = app({
    onTick: (fn) => { onTickCallback = fn; },
    secondsUntilLock: () => 187,
  });
  component.init();
  assert.equal(typeof onTickCallback, 'function');
  onTickCallback();
  assert.equal(component.secondsLeft, 187);
  assert.equal(component.countdown(), '3:07');
});

test('creating a vault seals a fresh key to the account and signs the metadata', async () => {
  const posted = [];
  const component = app({ isUnlocked: () => true }, {
    listVaults: async () => [],
    createVault: async (body) => { posted.push(body); return { ...body, wrapped_key: body.wrapped_key }; },
  });
  await component.unlock();
  component.newVaultName = 'Work';
  await component.createVault();
  assert.equal(posted.length, 1);
  assert.equal(posted[0].uuid, 'vault-uuid');
  assert.equal(posted[0].metadata_sig, 'signature');
  assert.deepEqual(posted[0].hpke_suite, { kem_id: 32, kdf_id: 1, aead_id: 2, mode: 0 });
  assert.equal(component.vaults.length, 1);
});

test('the vault name never leaves the browser in the clear', async () => {
  const posted = [];
  const component = app({ isUnlocked: () => true }, {
    listVaults: async () => [],
    createVault: async (body) => { posted.push(body); return body; },
  });
  await component.unlock();
  component.newVaultName = 'Work';
  await component.createVault();
  assert.equal(JSON.stringify(posted[0]).includes('Work'), false);
});

test('a refused creation leaves the dialog open with its message', async () => {
  const component = app({ isUnlocked: () => true }, {
    listVaults: async () => [],
    createVault: async () => { const e = new Error('x'); e.status = 400; throw e; },
  });
  await component.unlock();
  component.showCreate = true;
  component.newVaultName = 'Work';
  await component.createVault();
  assert.equal(component.showCreate, true);
  assert.ok(component.error);
});

test('an empty name is refused before anything is sealed', async () => {
  let called = 0;
  const component = app({ isUnlocked: () => true }, {
    listVaults: async () => [],
    createVault: async () => { called += 1; return {}; },
  });
  await component.unlock();
  component.newVaultName = '   ';
  await component.createVault();
  assert.equal(called, 0);
});

test('creating a vault while locked is refused', async () => {
  let called = 0;
  const component = app({ isUnlocked: () => false }, {
    listVaults: async () => [],
    createVault: async () => { called += 1; return {}; },
  });
  component.newVaultName = 'Work';
  await component.createVault();
  assert.equal(called, 0);
  assert.equal(component.state, 'locked');
});
