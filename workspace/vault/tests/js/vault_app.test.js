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
  const component = app();
  component.init();
  assert.equal(component.secretRequired, true);
});

test('typing the recovery key does not take the field away', () => {
  // The gate is decided at mount, never from the field's own value: it is
  // bound with x-model, so a gate reading it would close on the first
  // character and unmount the input mid-word. Pasting hid this - one input
  // event carries the whole key - so the check walks the value up from empty.
  const component = app();
  component.init();
  for (const value of ['0', '0E', '0ET', '0ETE']) {
    component.secretText = value;
    assert.equal(component.secretRequired, true, `gate closed at "${value}"`);
  }
});

test('the submit button waits for a required recovery key, not for any text', () => {
  const component = app();
  component.init();
  assert.equal(component.secretMissing(), true);
  component.secretText = '0ETE';
  assert.equal(component.secretMissing(), false);
});

test('a remembered key is used without asking for it again', () => {
  const component = app({ rememberedSecret: () => 'A'.repeat(53) });
  component.init();
  assert.equal(component.secretRequired, false);
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
    rememberedSecret: () => 'A'.repeat(53),
    unlock: async () => { const e = new Error('x'); e.reason = 'recovery-key'; throw e; },
    forgetDevice: () => { forgotten = true; },
  });
  component.init();
  assert.equal(component.secretRequired, false);
  await component.unlock();
  assert.equal(component.secretText, '');
  assert.equal(component.secretRequired, true);
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

test('a password failure on a remembered key hands the key back to be corrected', async () => {
  // A recovery key belonging to another account decodes cleanly and then fails
  // the same AEAD tag a mistyped password does, so nothing here can tell the
  // two apart. Leaving the field hidden makes the second case unrecoverable:
  // every attempt fails, the message blames the password, and the key that is
  // actually wrong is not on screen to be changed.
  const component = app({
    rememberedSecret: () => 'A'.repeat(53),
    unlock: async () => { const e = new Error('x'); e.reason = 'password'; throw e; },
  });
  component.init();
  assert.equal(component.secretRequired, false);
  await component.unlock();
  assert.equal(component.secretRequired, true);
  assert.equal(component.secretText, 'A'.repeat(53));
  assert.match(component.error, /recovery key/i);
});

test('a password failure without a remembered key does not grow a second field', async () => {
  const component = app({
    unlock: async () => { const e = new Error('x'); e.reason = 'password'; throw e; },
  });
  component.init();
  component.secretText = 'A'.repeat(53);
  await component.unlock();
  assert.equal(component.secretRequired, true);
  assert.match(component.error, /master password/i);
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

test('a lock while the list is loading does not put the names back on screen', async () => {
  // The listing is not atomic with the lock: an idle timeout or a hidden tab
  // can fire between the request going out and the answer being decrypted,
  // and by then the onLock callback that empties the list has already run.
  let unlocked = true;
  let release;
  const component = app(
    { isUnlocked: () => unlocked },
    {
      listVaults: () => new Promise((resolve) => {
        release = () => resolve([
          { uuid: 'v1', encrypted_name: 'n', metadata_sig: 's', wrapped_key: 'w' },
        ]);
      }),
    }
  );
  const loading = component.loadVaults();
  unlocked = false;
  component.vaults = [];
  release();
  await loading;
  assert.deepEqual([...component.vaults], []);
});

test('a retry after a lost answer creates one vault, not two', async () => {
  // The server matches on the UUID the client mints, which is the only reason
  // its conflict branch can turn a lost response into "already written". A
  // fresh UUID on the retry defeats that: the second request describes a
  // different vault, and the account ends up with two under two keys.
  let minted = 0;
  let fail = true;
  const posted = [];
  const component = app(
    { isUnlocked: () => true },
    {
      listVaults: async () => [],
      createVault: async (body) => {
        posted.push(body);
        if (fail) throw new Error('the answer never arrived');
        return body;
      },
    },
    { uuidV7: () => 'vault-uuid-' + ++minted }
  );
  await component.unlock();
  component.newVaultName = 'Work';
  await component.createVault();
  fail = false;
  await component.createVault();
  assert.equal(posted.length, 2);
  assert.equal(
    posted[0].uuid,
    posted[1].uuid,
    'the retry must carry the UUID the server may already have written'
  );
});

test('a conflict is the vault already existing, not a failure to report', async () => {
  let listed = [];
  const component = app(
    { isUnlocked: () => true },
    {
      listVaults: async () => listed,
      createVault: async () => {
        const err = new Error('conflict');
        err.status = 409;
        throw err;
      },
    }
  );
  await component.unlock();
  listed = [{ uuid: 'vault-uuid', encrypted_name: 'n', metadata_sig: 's', wrapped_key: 'w' }];
  component.newVaultName = 'Work';
  await component.createVault();
  assert.equal(component.error, '');
  assert.equal(component.showCreate, false);
  assert.equal(component.vaults.length, 1);
});

test('a lock during a listing is not reported as tampering', async () => {
  // verifyVaultMetadata refuses to run while locked, so an idle timeout fails
  // every row still in flight exactly as a forged signature would. The tamper
  // alert tells the user not to retype their password; it must never be what
  // an idle timeout produces.
  const component = app({
    isUnlocked: () => true,
    verifyVaultMetadata: async () => {
      const err = new Error('the vault is locked');
      err.reason = 'locked';
      throw err;
    },
  }, {
    listVaults: async () => [
      { uuid: 'v1', encrypted_name: 'AQ', wrapped_key: 'AQ', metadata_sig: 'AQ' },
    ],
  });
  await component.unlock();
  assert.deepEqual([...component.vaults], []);
  assert.equal(component.error, '');
});

test('a lock during a listing is not reported as a corrupt vault either', async () => {
  const component = app({
    isUnlocked: () => true,
    openVaultKey: async () => {
      const err = new Error('the vault is locked');
      err.reason = 'locked';
      throw err;
    },
  }, {
    listVaults: async () => [
      { uuid: 'v1', encrypted_name: 'AQ', wrapped_key: 'AQ', metadata_sig: 'AQ' },
    ],
  });
  await component.unlock();
  assert.deepEqual([...component.vaults], []);
  assert.equal(component.error, '');
});

test('a lock between writing a vault and showing it keeps the name off the screen', async () => {
  // The guard at the top of createVault is three awaits away from the push:
  // building the request, the round trip, and the decryption. A lock landing
  // in any of them has already emptied the list, and pushing would put a
  // decrypted name back into a component the lock just cleared.
  let unlocked = true;
  const component = app(
    { isUnlocked: () => unlocked },
    {
      listVaults: async () => [],
      createVault: async (body) => {
        unlocked = false;
        return body;
      },
    }
  );
  component.newVaultName = 'Work';
  await component.createVault();
  assert.deepEqual([...component.vaults], []);
  assert.equal(component.error, '');
  // The vault was written: the retry after the next unlock has to find it
  // under the same UUID rather than describe a second one.
  assert.equal(component.pendingVaultUuid, 'vault-uuid');
});

test('a lock closes the create dialog instead of reopening it on the next unlock', async () => {
  // The dialog is nested inside the unlocked subtree, so a lock tears it off
  // the screen without clearing showCreate - and re-unlocking rebuilds the
  // subtree with the flag still true.
  let onLock = null;
  const component = app({
    isUnlocked: () => true,
    onLock: (fn) => { onLock = fn; },
  });
  component.init();
  component.showCreate = true;
  component.newVaultName = 'Work';
  component.pendingVaultUuid = 'vault-uuid';
  onLock();
  assert.equal(component.showCreate, false);
  assert.equal(component.newVaultName, '');
  assert.equal(component.pendingVaultUuid, null);
});

test('a conflict on a UUID the account does not hold is not declared a success', async () => {
  // The 409 comes from a globally unique primary key: it says the UUID is
  // taken, not that the caller is the one holding it. Closing the dialog
  // without reading the reload back would report a vault that is not there.
  const component = app(
    { isUnlocked: () => true },
    {
      listVaults: async () => [],
      createVault: async () => {
        const err = new Error('conflict');
        err.status = 409;
        throw err;
      },
    }
  );
  component.showCreate = true;
  component.newVaultName = 'Work';
  await component.createVault();
  assert.equal(component.showCreate, true);
  assert.match(component.error, /could not be created/);
});
