// The unlock screen as a state machine. The session is stubbed - what it does
// is tested in session.test.js - so what is under test here is what the user
// sees at each step and what the screen refuses to do.
const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

const visited = [];

function app(session = {}, api = {}, crypto = {}) {
  visited.length = 0;
  const ctx = loadScripts([
    'workspace/vault/ui/static/vault/ui/js/vault_format.js',
    'workspace/vault/ui/static/vault/ui/js/vault_menu.js',
    'workspace/vault/ui/static/vault/ui/js/vault_unlock.js',
    'workspace/vault/ui/static/vault/ui/js/vault_reader.js',
    'workspace/vault/ui/static/vault/ui/js/vault_create.js',
    'workspace/vault/ui/static/vault/ui/js/vault_update.js',
    'workspace/vault/ui/static/vault/ui/js/vault_app.js',
  ], {
    vaultSession: {
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
      setIdleTimeout() {},
      ...session,
    },
    vaultApi: { listVaults: async () => [], createVault: async () => ({}), ...api },
    vaultCrypto: {
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
    document: {
      addEventListener() {},
      getElementById: () => null,
    },
    location: { assign: (url) => visited.push(url) },
    addEventListener() {},
    localStorage: (globalThis.__vaultAppStore = globalThis.__vaultAppStore || {
      values: {},
      getItem(key) {
        return Object.prototype.hasOwnProperty.call(this.values, key)
          ? this.values[key]
          : null;
      },
      setItem(key, value) { this.values[key] = String(value); },
      removeItem(key) { delete this.values[key]; },
    }),
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
  // vaultSession reports isUnlocked() === true in this harness: the screen
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
  component.openCreateDialog();
  component.newVault.name = 'Work';
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
  component.openCreateDialog();
  component.newVault.name = 'Work';
  await component.createVault();
  assert.equal(JSON.stringify(posted[0]).includes('Work'), false);
});

test('a refused creation leaves the dialog open with its message', async () => {
  const component = app({ isUnlocked: () => true }, {
    listVaults: async () => [],
    createVault: async () => { const e = new Error('x'); e.status = 400; throw e; },
  });
  await component.unlock();
  component.openCreateDialog();
  component.newVault.name = 'Work';
  await component.createVault();
  assert.notStrictEqual(component.newVault, null);
  assert.ok(component.error);
});

test('an empty name is refused before anything is sealed', async () => {
  let called = 0;
  const component = app({ isUnlocked: () => true }, {
    listVaults: async () => [],
    createVault: async () => { called += 1; return {}; },
  });
  await component.unlock();
  component.openCreateDialog();
  component.newVault.name = '   ';
  await component.createVault();
  assert.equal(called, 0);
});

test('creating a vault while locked is refused', async () => {
  let called = 0;
  const component = app({ isUnlocked: () => false }, {
    listVaults: async () => [],
    createVault: async () => { called += 1; return {}; },
  });
  component.openCreateDialog();
  component.newVault.name = 'Work';
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
  component.openCreateDialog();
  component.newVault.name = 'Work';
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
  component.openCreateDialog();
  component.newVault.name = 'Work';
  await component.createVault();
  assert.equal(component.error, '');
  assert.strictEqual(component.newVault, null);
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
  component.openCreateDialog();
  component.newVault.name = 'Work';
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
  component.openCreateDialog();
  component.newVault.name = 'Work';
  component.pendingVaultUuid = 'vault-uuid';
  onLock();
  assert.strictEqual(component.newVault, null);
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
  component.openCreateDialog();
  component.newVault.name = 'Work';
  await component.createVault();
  assert.notStrictEqual(component.newVault, null);
  assert.match(component.error, /could not be created/);
});

// ---------------------------------------------------------------- vault menus

const ROWS = [
  { uuid: 'v-1', encrypted_name: 'AQ', metadata_sig: 'AQ', wrapped_key: 'AQ', is_favorite: false },
  { uuid: 'v-2', encrypted_name: 'AQ', metadata_sig: 'AQ', wrapped_key: 'AQ', is_favorite: true },
];

// The set the registry really answers with, so the harness cannot drift from
// workspace/vault/actions/vault.py without a test noticing.
const OWNER_ACTIONS = [
  { id: 'rename', label: 'Rename', icon: 'pencil', category: 'edit', css_class: '', bulk: false },
  { id: 'set_appearance', label: 'Icon and colour', icon: 'palette', category: 'edit', css_class: '', bulk: false },
  { id: 'favorite', label: 'Add to favourites', icon: 'star', category: 'organize', css_class: '', bulk: false },
  { id: 'unfavorite', label: 'Remove from favourites', icon: 'star-off', category: 'organize', css_class: '', bulk: false },
  { id: 'delete', label: 'Delete vault', icon: 'trash-2', category: 'danger', css_class: 'text-error', bulk: false },
];

function listing(api = {}) {
  return app(
    { isUnlocked: () => true },
    {
      listVaults: async () => ROWS,
      fetchVaultActions: async () => ({ 'v-1': OWNER_ACTIONS, 'v-2': OWNER_ACTIONS }),
      updateVault: async () => ({}),
      deleteVault: async () => null,
      ...api,
    },
  );
}

test('the menu of a vault is what the endpoint answered, never a fixed list', async () => {
  const component = listing();
  await component.loadVaults();
  assert.deepStrictEqual(
    Array.from(component.actionsFor(component.vaults[0]).map((a) => a.id)),
    ['rename', 'set_appearance', 'favorite', 'delete'],
  );
});

test('a vault the caller may not act on offers nothing', async () => {
  // A member holding a key wrap opens the vault and may rewrite nothing
  // about it. The empty answer has to render as an empty menu, not as a
  // menu the client fills in from what it assumes.
  const component = listing({ fetchVaultActions: async () => ({ 'v-1': [], 'v-2': [] }) });
  await component.loadVaults();
  assert.deepStrictEqual(Array.from(component.actionsFor(component.vaults[0])), []);
});

test('only the favourite verb matching the row is offered', async () => {
  // The registry answers what the caller may do, not what the row is; both
  // verbs come back and the client picks, because it already holds the flag.
  const component = listing();
  await component.loadVaults();
  const ids = (vault) => Array.from(component.actionsFor(vault).map((a) => a.id));
  assert.ok(ids(component.vaults[0]).includes('favorite'));
  assert.ok(!ids(component.vaults[0]).includes('unfavorite'));
  assert.ok(ids(component.vaults[1]).includes('unfavorite'));
  assert.ok(!ids(component.vaults[1]).includes('favorite'));
});

test('an answer that arrives after a newer listing is discarded', async () => {
  // Two listings in flight and the slower one landing last would leave the
  // menus describing vaults that are no longer on screen.
  let resolveFirst;
  let call = 0;
  const component = listing({
    fetchVaultActions: async () => {
      call += 1;
      if (call === 1) return new Promise((resolve) => { resolveFirst = resolve; });
      return { 'v-1': [], 'v-2': [] };
    },
  });

  const stale = component.loadVaults();
  await component.loadVaults();
  resolveFirst({ 'v-1': OWNER_ACTIONS, 'v-2': OWNER_ACTIONS });
  await stale;

  assert.deepStrictEqual(Array.from(component.actionsFor(component.vaults[0])), []);
});

test('locking takes the menus away with the list', async () => {
  // They describe rows that are gone, and a menu outliving its vault is a
  // click on a vault the page can no longer name.
  const callbacks = [];
  const component = app(
    { isUnlocked: () => true, onLock: (cb) => callbacks.push(cb) },
    {
      listVaults: async () => ROWS,
      fetchVaultActions: async () => ({ 'v-1': OWNER_ACTIONS, 'v-2': OWNER_ACTIONS }),
    },
  );
  component.init();
  await component.loadVaults();
  callbacks.forEach((cb) => cb());
  assert.deepStrictEqual(Array.from(component.actionsFor({ uuid: 'v-1' })), []);
});

test('a listing whose action lookup fails still shows the vaults', async () => {
  // The names are open and the page is usable; losing the menus is worth
  // saying nothing about rather than blanking a working list.
  const component = listing({
    fetchVaultActions: async () => { throw new Error('offline'); },
  });
  await component.loadVaults();
  assert.equal(component.vaults.length, 2);
  assert.deepStrictEqual(Array.from(component.actionsFor(component.vaults[0])), []);
});

test('an action the endpoint did not offer is refused by the handler too', async () => {
  // Defence in depth: a menu built from a stale answer must not produce a
  // request the server is about to refuse anyway.
  let called = false;
  const component = listing({
    fetchVaultActions: async () => ({ 'v-1': [], 'v-2': [] }),
    updateVault: async () => { called = true; return {}; },
  });
  await component.loadVaults();
  await component.runVaultAction({ id: 'favorite' }, component.vaults[0]);
  assert.equal(called, false);
});

test('favouriting a vault re-signs its whole metadata', async () => {
  // is_favorite lives inside the signed payload, so there is no cheap write:
  // the row is re-described and re-signed or it stops verifying.
  const sent = [];
  const component = listing({
    updateVault: async (uuid, body) => { sent.push([uuid, body]); return {}; },
  });
  await component.loadVaults();
  await component.runVaultAction({ id: 'favorite' }, component.vaults[0]);
  assert.equal(sent[0][0], 'v-1');
  assert.equal(sent[0][1].is_favorite, true);
  assert.ok(sent[0][1].metadata_sig);
});

test('deleting a vault asks first and reloads the list', async () => {
  const deleted = [];
  const component = listing({
    deleteVault: async (uuid) => { deleted.push(uuid); return null; },
  });
  await component.loadVaults();
  await component.runVaultAction({ id: 'delete' }, component.vaults[0]);
  assert.deepStrictEqual(Array.from(deleted), ['v-1']);
});

test('a refused confirmation deletes nothing', async () => {
  const deleted = [];
  const component = listing({ deleteVault: async (uuid) => { deleted.push(uuid); } });
  component.confirm = async () => false;
  await component.loadVaults();
  await component.runVaultAction({ id: 'delete' }, component.vaults[0]);
  assert.deepStrictEqual(Array.from(deleted), []);
});

test('rename opens a dialog rather than writing straight away', async () => {
  const sent = [];
  const component = listing({ updateVault: async (u, b) => { sent.push(b); return {}; } });
  await component.loadVaults();
  await component.runVaultAction({ id: 'rename' }, component.vaults[0]);
  assert.equal(component.vaultDialog.mode, 'rename');
  assert.equal(component.vaultDialog.name, 'Personal');
  assert.deepStrictEqual(Array.from(sent), []);
});

test('saving the dialog re-signs the vault under its new name', async () => {
  const sent = [];
  const component = listing({ updateVault: async (u, b) => { sent.push([u, b]); return {}; } });
  await component.loadVaults();
  await component.runVaultAction({ id: 'rename' }, component.vaults[0]);
  component.vaultDialog.name = 'Archives';
  await component.saveVaultDialog();
  assert.equal(sent[0][0], 'v-1');
  assert.ok(sent[0][1].metadata_sig);
  assert.equal(component.vaultDialog, null);
});

test('an empty name saves nothing', async () => {
  // A vault with no name is one the user cannot tell apart from another.
  const sent = [];
  const component = listing({ updateVault: async (u, b) => { sent.push(b); return {}; } });
  await component.loadVaults();
  await component.runVaultAction({ id: 'rename' }, component.vaults[0]);
  component.vaultDialog.name = '   ';
  await component.saveVaultDialog();
  assert.deepStrictEqual(Array.from(sent), []);
  assert.notEqual(component.vaultDialog, null);
});

test('the appearance dialog stores the role name, not the css class', async () => {
  // The picker's markup works in classes; the vault's signed metadata holds
  // the role. Converting on the way out is what lets the shared partial be
  // reused without widening what the server accepts.
  const sent = [];
  const component = listing({ updateVault: async (u, b) => { sent.push(b); return {}; } });
  await component.loadVaults();
  await component.runVaultAction({ id: 'set_appearance' }, component.vaults[0]);
  component.selectColor('text-success');
  component.selectIcon('briefcase');
  await component.saveVaultDialog();
  assert.equal(sent[0].color, 'success');
  assert.equal(sent[0].icon, 'briefcase');
});

test('a locked session closes the dialog and writes nothing', async () => {
  const sent = [];
  const component = listing({ updateVault: async (u, b) => { sent.push(b); return {}; } });
  await component.loadVaults();
  await component.runVaultAction({ id: 'rename' }, component.vaults[0]);
  component.vaultDialog.name = 'Archives';
  component.closeVaultDialog();
  await component.saveVaultDialog();
  assert.deepStrictEqual(Array.from(sent), []);
});

// --- the listing: filters, sort, views, preferences -------------------------

const LISTING_ROWS = [
  { uuid: 'v-1', name: 'Personal', description: 'Everyday logins', is_favorite: true, created_at: '2026-01-02' },
  { uuid: 'v-2', name: 'Work', description: 'Infrastructure accounts', is_favorite: false, created_at: '2026-03-04' },
  { uuid: 'v-3', name: 'Archive', description: '', is_favorite: false, created_at: '2026-02-03' },
];

function listed(component, rows = LISTING_ROWS) {
  component.vaults = rows.map((row) => Object.assign({}, row));
  return component;
}

const shown = (component) => Array.from(component.visibleVaults(), (v) => v.name);

test('the sidebar view narrows the listing to favourites', () => {
  const component = listed(app());
  assert.deepStrictEqual(shown(component), ['Personal', 'Work', 'Archive']);
  component.filter = 'favorites';
  assert.deepStrictEqual(shown(component), ['Personal']);
});

test('the search reads the name and the description', () => {
  // The description is decrypted on this side like the name, so there is no
  // reason for the filter to see one and not the other.
  const component = listed(app());
  component.search = 'infra';
  assert.deepStrictEqual(shown(component), ['Work']);
  component.search = 'archive';
  assert.deepStrictEqual(shown(component), ['Archive']);
});

test('sorting is off until it is asked for, then it holds a direction', () => {
  const component = listed(app());
  component.sortField = 'name';
  assert.deepStrictEqual(shown(component), ['Archive', 'Personal', 'Work']);
  component.sortDir = 'desc';
  assert.deepStrictEqual(shown(component), ['Work', 'Personal', 'Archive']);
});

test('sorting never reorders the listing it was asked about', () => {
  // Sorting the array in place would reorder the component's own data as a
  // side effect of asking what to display.
  const component = listed(app());
  component.sortField = 'name';
  component.visibleVaults();
  assert.deepStrictEqual(
    Array.from(component.vaults, (v) => v.name),
    ['Personal', 'Work', 'Archive'],
  );
});

test('the status line counts what is shown and what could not be opened', () => {
  const component = listed(app(), [
    ...LISTING_ROWS,
    { uuid: 'v-4', name: '', tampered: true },
    { uuid: 'v-5', name: '', unopenable: true },
  ]);
  const line = component.statusLine();
  assert.match(line, /3 vaults/);
  assert.match(line, /1 favourite/);
  assert.match(line, /2 unavailable/);
});

test('a vault that will not open is never in the listing', () => {
  const component = listed(app(), [
    LISTING_ROWS[0],
    { uuid: 'v-4', name: '', tampered: true },
  ]);
  assert.deepStrictEqual(shown(component), ['Personal']);
  assert.deepStrictEqual(
    Array.from(component.unavailableVaults(), (v) => v.uuid),
    ['v-4'],
  );
});

test('the view mode survives a reload', () => {
  const component = app();
  component.init();
  assert.equal(component.viewMode, 'list');
  component.setViewMode('grid');
  const second = app();
  second.init();
  assert.equal(second.viewMode, 'grid');
});

test('forgetting the device drops the remembered key', async () => {
  const forgotten = [];
  const component = app({ forgetDevice: () => forgotten.push(true) });
  component.init();
  component.confirm = async () => true;
  await component.forgetDevice();
  assert.deepStrictEqual(Array.from(forgotten), [true]);
});

test('a refused confirmation keeps the remembered key', async () => {
  const forgotten = [];
  const component = app({ forgetDevice: () => forgotten.push(true) });
  component.init();
  component.confirm = async () => false;
  await component.forgetDevice();
  assert.deepStrictEqual(Array.from(forgotten), []);
});

test('creating carries the icon, the colour and the description', async () => {
  const bodies = [];
  const component = app({ isUnlocked: () => true }, {
    createVault: async (body) => { bodies.push(body); return { uuid: 'vault-uuid' }; },
    listVaults: async () => [],
  });
  component.init();
  component.openCreateDialog();
  component.newVault.name = 'Work';
  component.newVault.description = 'Infra';
  component.selectIcon('briefcase');
  component.selectColor('text-info');
  await component.createVault();
  assert.equal(bodies.length, 1);
  assert.equal(bodies[0].icon, 'briefcase');
  assert.equal(bodies[0].color, 'info');
});

test('a vault created as a favourite is favourited by the write after it', async () => {
  // The create endpoint sets is_favorite itself and refuses a signature over
  // anything else, so the checkbox is honoured by the update that follows.
  const calls = [];
  const component = app({ isUnlocked: () => true }, {
    createVault: async () => { calls.push('create'); return { uuid: 'vault-uuid' }; },
    updateVault: async (uuid, body) => {
      calls.push('update:' + (body.is_favorite ? 'favorite' : 'plain'));
      return {};
    },
    listVaults: async () => [],
  });
  component.init();
  component.openCreateDialog();
  component.newVault.name = 'Work';
  component.newVault.favorite = true;
  await component.createVault();
  assert.deepStrictEqual(Array.from(calls), ['create', 'update:favorite']);
});

test('a vault created without the checkbox writes once', async () => {
  const calls = [];
  const component = app({ isUnlocked: () => true }, {
    createVault: async () => { calls.push('create'); return { uuid: 'vault-uuid' }; },
    updateVault: async () => { calls.push('update'); return {}; },
    listVaults: async () => [],
  });
  component.init();
  component.openCreateDialog();
  component.newVault.name = 'Work';
  await component.createVault();
  assert.deepStrictEqual(Array.from(calls), ['create']);
});

test('a failed favourite leaves the vault created and says nothing alarming', async () => {
  // The vault exists; only the flag did not land. Reporting a failed creation
  // would send the user to create a second one.
  const component = app({ isUnlocked: () => true }, {
    createVault: async () => ({ uuid: 'vault-uuid' }),
    updateVault: async () => { throw new Error('refused'); },
    listVaults: async () => [],
  });
  component.init();
  component.openCreateDialog();
  component.newVault.name = 'Work';
  component.newVault.favorite = true;
  await component.createVault();
  assert.equal(component.error, '');
  assert.equal(component.newVault, null);
});

// --- the sidebar and its preferences ----------------------------------------

test('a vault owned by another account is shown as a membership', () => {
  const component = app({ accountUuid: () => 'account-1' });
  assert.equal(component.isMember({ owner_account_uuid: 'account-2' }), true);
  assert.equal(component.isMember({ owner_account_uuid: 'account-1' }), false);
});

test('the preferences come from the page, with defaults behind them', () => {
  const component = app();
  component.init();
  assert.equal(component.prefs.lockAfterMinutes, 5);
  assert.equal(component.prefs.defaultSort, 'default');
});

test('a changed preference is written and applied at once', async () => {
  const written = [];
  const component = app();
  component.init();
  component.putSetting = async (key, value) => { written.push([key, value]); };
  await component.updatePref('default_sort', 'name');
  assert.deepStrictEqual(Array.from(written), [['default_sort', 'name']]);
  assert.equal(component.prefs.defaultSort, 'name');
  // Applied, not merely stored: the listing sorts by it straight away.
  assert.equal(component.sortField, 'name');
});

test('the lock delay reaches the session, not just the preference', async () => {
  // Stored and never handed over, it would be a setting that changes nothing
  // until the page is reloaded.
  const delays = [];
  const component = app({ setIdleTimeout: (minutes) => delays.push(minutes) });
  component.init();
  // init hands over the stored delay; what is under test is the change.
  delays.length = 0;
  component.putSetting = async () => {};
  await component.updatePref('lock_after_minutes', 15);
  assert.deepStrictEqual(Array.from(delays), [15]);
  assert.equal(component.prefs.lockAfterMinutes, 15);
});

test('a preference the server refuses is put back, not left lying', async () => {
  const component = app();
  component.init();
  component.putSetting = async () => { throw new Error('refused'); };
  await component.updatePref('default_sort', 'name');
  assert.equal(component.prefs.defaultSort, 'default');
  assert.match(component.error, /could not be saved/i);
});

// --- selection, tiles and context menus -------------------------------------

test('a vault can be selected, and select-all covers what is shown', () => {
  const component = listed(app());
  assert.equal(component.selectAllState(), 'none');
  component.toggleSelection('v-1');
  assert.equal(component.selectAllState(), 'partial');
  component.selectAll();
  assert.deepStrictEqual(Array.from(component.selected).sort(), ['v-1', 'v-2', 'v-3']);
  assert.equal(component.selectAllState(), 'all');
  component.clearSelection();
  assert.deepStrictEqual(Array.from(component.selected), []);
});

test('select-all under a filter takes only what the filter shows', () => {
  const component = listed(app());
  component.filter = 'favorites';
  component.selectAll();
  assert.deepStrictEqual(Array.from(component.selected), ['v-1']);
});

test('a vault that leaves the listing leaves the selection with it', () => {
  // Otherwise the bar counts rows nobody can see, and acts on them.
  const component = listed(app());
  component.selectAll();
  component.search = 'personal';
  assert.deepStrictEqual(Array.from(component.selectedVaults(), (v) => v.uuid), ['v-1']);
});

test('the bulk bar offers only what every selected vault offers', () => {
  const component = listed(app());
  component.vaultActions = {
    'v-1': [{ id: 'favorite', bulk: true }, { id: 'delete', bulk: true }, { id: 'rename', bulk: false }],
    'v-2': [{ id: 'delete', bulk: true }],
  };
  component.toggleSelection('v-1');
  component.toggleSelection('v-2');
  assert.deepStrictEqual(
    Array.from(component.bulkActions(), (action) => action.id),
    ['delete'],
  );
});

test('the tile size is remembered and clamped to the scale', () => {
  const component = app();
  component.init();
  assert.equal(component.tileSize, 3);
  component.setTileSize(5);
  const second = app();
  second.init();
  assert.equal(second.tileSize, 5);
  // Anything off the 1..5 scale is a bug or a stale preference, and a tile of
  // zero pixels is not a smaller tile.
  component.setTileSize(0);
  assert.equal(component.tileSize, 5);
  component.setTileSize(99);
  assert.equal(component.tileSize, 5);
});

test('the tile size drives the geometry rather than a class per step', () => {
  const component = app();
  component.init();
  component.setTileSize(1);
  const small = component.tileMinWidth();
  component.setTileSize(5);
  assert.ok(component.tileMinWidth() > small);
});

test('a right-click on a vault opens its menu where it was raised', () => {
  const component = listed(app());
  component.openVaultMenu({ clientX: 40, clientY: 90, preventDefault() {} }, component.vaults[0]);
  assert.equal(component.menu.open, true);
  assert.equal(component.menu.vault.uuid, 'v-1');
  assert.equal(component.menu.x, 40);
  assert.equal(component.backgroundMenu.open, false);
});

test('a right-click on the empty listing opens the listing menu', () => {
  const component = listed(app());
  component.openBackgroundMenu({ clientX: 10, clientY: 20, preventDefault() {} });
  assert.equal(component.backgroundMenu.open, true);
  assert.equal(component.menu.open, false);
});

test('opening one menu closes the other', () => {
  const component = listed(app());
  component.openVaultMenu({ clientX: 0, clientY: 0, preventDefault() {} }, component.vaults[0]);
  component.openBackgroundMenu({ clientX: 0, clientY: 0, preventDefault() {} });
  assert.equal(component.menu.open, false);
  component.openVaultMenu({ clientX: 0, clientY: 0, preventDefault() {} }, component.vaults[1]);
  assert.equal(component.backgroundMenu.open, false);
});

test('the heading names the view being looked at', () => {
  const component = app();
  assert.equal(component.heading(), 'My vaults');
  component.filter = 'favorites';
  assert.equal(component.heading(), 'Favorites');
});

test('clicking a row opens the vault', () => {
  const component = listed(app());
  component.openVault(component.vaults[0], { });
  assert.deepStrictEqual(Array.from(visited), ['/vault/v-1']);
});

test('a vault that will not open is never navigated to', () => {
  // It has no contents to show and no key to show them with; following it
  // would land on a browser screen that can only report the same failure.
  const component = listed(app(), [{ uuid: 'v-9', name: '', tampered: true }]);
  visited.length = 0;
  component.openVault(component.vaults[0], {});
  assert.deepStrictEqual(Array.from(visited), []);
});

test('the favourite button is offered only when the registry offers the verb', () => {
  const component = listed(app());
  component.vaultActions = { 'v-1': [{ id: 'favorite', bulk: true }], 'v-2': [] };
  assert.equal(component.canFavorite(component.vaults[0]), true);
  assert.equal(component.canFavorite(component.vaults[1]), false);
});

test('the star writes the verb the row is missing', async () => {
  // One component per write: a write reloads the listing, and the reload is
  // what makes the action map current again.
  async function star(isFavorite) {
    const written = [];
    const component = listed(
      app({ isUnlocked: () => true }, {
        updateVault: async (uuid, body) => { written.push([uuid, body.is_favorite]); return {}; },
        listVaults: async () => [],
      }),
      [{ uuid: 'v-1', name: 'A', is_favorite: isFavorite }],
    );
    component.vaultActions = { 'v-1': [{ id: 'favorite' }, { id: 'unfavorite' }] };
    await component.toggleFavorite(component.vaults[0]);
    return written;
  }

  assert.deepStrictEqual(Array.from(await star(true)), [['v-1', false]]);
  assert.deepStrictEqual(Array.from(await star(false)), [['v-1', true]]);
});

test('sorting by entry count reads the number the server gave', () => {
  const component = listed(app(), [
    { uuid: 'v-1', name: 'A', entry_count: 9 },
    { uuid: 'v-2', name: 'B', entry_count: 2 },
  ]);
  component.sortField = 'entries';
  assert.deepStrictEqual(shown(component), ['B', 'A']);
  component.sortDir = 'desc';
  assert.deepStrictEqual(shown(component), ['A', 'B']);
});

test('a bulk verb runs over every selected vault and asks once', async () => {
  const deleted = [];
  let asked = 0;
  const component = listed(app({ isUnlocked: () => true }, {
    deleteVault: async (uuid) => { deleted.push(uuid); return null; },
    listVaults: async () => [],
  }));
  component.vaultActions = {
    'v-1': [{ id: 'delete', bulk: true }],
    'v-2': [{ id: 'delete', bulk: true }],
    'v-3': [{ id: 'delete', bulk: true }],
  };
  component.confirm = async () => { asked += 1; return true; };
  component.toggleSelection('v-1');
  component.toggleSelection('v-2');
  await component.runBulkVaultAction({ id: 'delete' });
  assert.deepStrictEqual(Array.from(deleted).sort(), ['v-1', 'v-2']);
  assert.equal(asked, 1);
});

test('a refused confirmation deletes nothing', async () => {
  const deleted = [];
  const component = listed(app({ isUnlocked: () => true }, {
    deleteVault: async (uuid) => { deleted.push(uuid); return null; },
    listVaults: async () => [],
  }));
  component.vaultActions = { 'v-1': [{ id: 'delete', bulk: true }] };
  component.confirm = async () => false;
  component.toggleSelection('v-1');
  await component.runBulkVaultAction({ id: 'delete' });
  assert.deepStrictEqual(Array.from(deleted), []);
});

test('a bulk verb the selection was not offered is refused', async () => {
  const deleted = [];
  const component = listed(app({ isUnlocked: () => true }, {
    deleteVault: async (uuid) => { deleted.push(uuid); return null; },
    listVaults: async () => [],
  }));
  // v-2 was offered nothing, so the bar offers nothing for the pair.
  component.vaultActions = { 'v-1': [{ id: 'delete', bulk: true }], 'v-2': [] };
  component.confirm = async () => true;
  component.toggleSelection('v-1');
  component.toggleSelection('v-2');
  await component.runBulkVaultAction({ id: 'delete' });
  assert.deepStrictEqual(Array.from(deleted), []);
});

test('a vault just created carries the actions the endpoint answers for it', async () => {
  // The map is keyed by uuid and was filled before this vault existed, so
  // without a second ask the new row offers nothing at all.
  const asked = [];
  const component = app({ isUnlocked: () => true }, {
    listVaults: async () => [],
    createVault: async (body) => ({ ...body }),
    fetchVaultActions: async (uuids) => {
      asked.push(uuids);
      return { 'vault-uuid': [{ id: 'rename', label: 'Rename', icon: 'pen', category: 'edit' }] };
    },
  });
  await component.unlock();
  component.openCreateDialog();
  component.newVault.name = 'Work';
  await component.createVault();
  assert.deepEqual(Array.from(asked[asked.length - 1]), ['vault-uuid']);
  assert.deepEqual(
    component.actionsFor(component.vaults[0]).map((a) => a.id),
    ['rename']
  );
});

test('reset puts the listing back to the default this account saved', async () => {
  const component = app({ isUnlocked: () => true });
  await component.unlock();
  component.prefs.defaultSort = 'name';
  component.search = 'work';
  component.filter = 'favorites';
  component.sortField = 'entries';
  component.sortDir = 'desc';
  component.resetAll();
  assert.equal(component.search, '');
  assert.equal(component.filter, 'all');
  assert.equal(component.sortField, 'name');
  assert.equal(component.sortDir, 'asc');
});
