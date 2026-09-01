// Managing a vault from the switcher, which is the only place it happens now.
//
// The rules are the ones the listing encoded, and the one worth pinning
// hardest is that nothing here decides what a vault may do - the action
// endpoint does, and a menu built from anything else is a menu that offers a
// request the server is about to refuse.
const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

const VAULT = {
  uuid: 'v-1', name: 'Personal', icon: 'lock', color: 'error',
  is_favorite: false, wrapped_key: 'AQ', key_version: 1,
};
const OTHER = {
  uuid: 'v-2', name: 'Work', icon: 'briefcase', color: 'info',
  is_favorite: true, wrapped_key: 'AQ', key_version: 1,
};

const ACTION = {
  rename: { id: 'rename', label: 'Rename', icon: 'pencil', css_class: '' },
  set_appearance: { id: 'set_appearance', label: 'Icon and colour', icon: 'palette', css_class: '' },
  favorite: { id: 'favorite', label: 'Add to favourites', icon: 'star', css_class: '' },
  unfavorite: { id: 'unfavorite', label: 'Remove from favourites', icon: 'star-off', css_class: '' },
  delete: { id: 'delete', label: 'Delete', icon: 'trash-2', css_class: 'text-error' },
};

const EVERY_ACTION = [ACTION.rename, ACTION.set_appearance, ACTION.favorite, ACTION.delete];

function switcher(overrides = {}) {
  const calls = [];
  const bodies = [];
  const ctx = loadScripts(
    [
      'workspace/vault/ui/static/vault/ui/js/vault_menu.js',
      'workspace/vault/ui/static/vault/ui/js/vault_switcher.js',
    ],
    {
      vaultApi: {
        fetchVaultActions: async (uuids) =>
          Object.fromEntries(uuids.map((uuid) => [uuid, EVERY_ACTION])),
        updateVault: async (uuid, body) => {
          calls.push('put:' + uuid);
          bodies.push(body);
          return body;
        },
        createVault: async () => { calls.push('post'); return { uuid: 'v-new' }; },
        deleteVault: async (uuid) => { calls.push('delete:' + uuid); return null; },
        ...overrides.api,
      },
      buildVaultUpdateRequest: async (session, vault, changes) =>
        Object.assign({ uuid: vault.uuid }, changes),
      buildVaultCreateRequest: async (session, draft, uuid) =>
        Object.assign({ uuid: uuid }, draft),
      vaultReader: {
        readVault: async (session, row) => Object.assign({ name: 'Created' }, row),
      },
      vaultCrypto: { uuidV7: () => 'v-new' },
      vaultSession: { isUnlocked: () => true, accountUuid: () => 'account-1' },
      history: { replaceState() {} },
      localStorage: {
        values: {},
        getItem(key) {
          return Object.prototype.hasOwnProperty.call(this.values, key)
            ? this.values[key]
            : null;
        },
        setItem(key, value) { this.values[key] = String(value); },
      },
    },
  );

  // The host component, reduced to what the mixin actually reaches for. The
  // real one is vaultBrowser(); spreading the mixin over a stand-in is what
  // keeps this file about the switcher rather than about the browser.
  const component = Object.assign(
    {
      vaults: [VAULT, OTHER],
      openVault: VAULT,
      vaultUuid: 'v-1',
      busy: false,
      error: '',
      loaded: 0,
      load: async function () { this.loaded += 1; },
      confirm: async () => true,
      rememberVault: function (uuid) { this.vaultUuid = uuid; },
      $nextTick: null,
    },
    ctx.vaultSwitcherMixin(),
    overrides.component,
  );
  return { ctx, component, calls, bodies };
}

// --- what a vault may do comes from the endpoint ----------------------------

test('the menu of a vault is whatever the endpoint answered for it', async () => {
  const { component } = switcher();
  await component.loadVaultActions();
  const ids = component.vaultActionsFor(VAULT).map((action) => action.id);
  assert.ok(ids.includes('rename'));
  assert.ok(ids.includes('delete'));
});

test('only one of the two favourite verbs is ever offered', async () => {
  // The registry answers what the caller may do, not what the row is.
  // Choosing between two exclusives from a flag the client already holds is
  // not a rule copied from the server.
  const { component } = switcher({
    api: {
      fetchVaultActions: async (uuids) =>
        Object.fromEntries(uuids.map((uuid) => [uuid, [ACTION.favorite, ACTION.unfavorite]])),
    },
  });
  await component.loadVaultActions();
  assert.deepStrictEqual(
    component.vaultActionsFor(VAULT).map((action) => action.id), ['favorite'],
  );
  assert.deepStrictEqual(
    component.vaultActionsFor(OTHER).map((action) => action.id), ['unfavorite'],
  );
});

test('a vault the endpoint said nothing about offers nothing', async () => {
  const { component } = switcher({ api: { fetchVaultActions: async () => ({}) } });
  await component.loadVaultActions();
  assert.deepStrictEqual(Array.from(component.vaultActionsFor(VAULT)), []);
});

test('a refused action list leaves the page usable', async () => {
  // The names are open and the switcher still switches. Blanking a working
  // page over a lost menu would cost the user more than the menu.
  const { component } = switcher({
    api: { fetchVaultActions: async () => { throw new Error('refused'); } },
  });
  await component.loadVaultActions();
  assert.deepStrictEqual(Array.from(component.vaultActionsFor(VAULT)), []);
  assert.equal(component.error, '');
});

test('an action the menu no longer offers is never carried out', async () => {
  // The menu was built from the endpoint, but it may have been built a while
  // ago: asking again stops a stale menu producing a refused request.
  const { component, calls } = switcher({ api: { fetchVaultActions: async () => ({}) } });
  await component.loadVaultActions();
  await component.runVaultAction(ACTION.delete, VAULT);
  assert.deepStrictEqual(Array.from(calls), []);
});

// --- switching, which is not a navigation -----------------------------------

test('switching vault reloads without navigating', async () => {
  const { component } = switcher();
  component.switcherOpen = true;
  await component.switchVault(OTHER);
  assert.equal(component.vaultUuid, 'v-2');
  assert.equal(component.loaded, 1);
  assert.equal(component.switcherOpen, false, 'the popover closes behind the choice');
});

test('switching to the vault already open does nothing', async () => {
  const { component } = switcher();
  await component.switchVault(VAULT);
  assert.equal(component.loaded, 0);
});

test('a vault whose signature failed cannot be switched to', async () => {
  // It has no name to show and no contents to read: opening one would swap a
  // working screen for a banner.
  const { component } = switcher();
  await component.switchVault({ uuid: 'v-bad', tampered: true });
  assert.equal(component.loaded, 0);
  assert.equal(component.vaultUuid, 'v-1');
});

test('the search field appears only past a handful of vaults', () => {
  const { component } = switcher();
  assert.equal(component.switcherNeedsSearch(), false);
  component.vaults = Array.from({ length: 8 }, (_, i) => ({ uuid: 'v' + i, name: 'V' + i }));
  assert.equal(component.switcherNeedsSearch(), true);
});

// --- writes that re-sign ----------------------------------------------------

test('favouriting re-signs the vault rather than asking the server to flip a column', async () => {
  const { component, calls } = switcher();
  await component.loadVaultActions();
  await component.runVaultAction(ACTION.favorite, VAULT);
  assert.deepStrictEqual(Array.from(calls), ['put:v-1']);
  assert.equal(component.loaded, 1);
});

test('renaming opens the dialog rather than writing straight away', async () => {
  const { component, calls } = switcher();
  await component.loadVaultActions();
  await component.runVaultAction(ACTION.rename, VAULT);
  assert.equal(component.vaultDialog.mode, 'rename');
  assert.equal(component.vaultDialog.name, 'Personal');
  assert.deepStrictEqual(Array.from(calls), []);
});

test('the appearance dialog carries the vault colour back as a css class', async () => {
  // The picker's markup works in CSS classes; the signed metadata holds the
  // bare role. Converting at the edges is what lets the shared partial be
  // reused without widening what the server accepts.
  const { component } = switcher();
  await component.loadVaultActions();
  await component.runVaultAction(ACTION.set_appearance, VAULT);
  assert.equal(component.selectedColor, 'text-error');
  assert.equal(component.selectedIcon, 'lock');
});

test('saving the dialog writes the bare role, not the css class', async () => {
  const { component, bodies } = switcher();
  component.openVaultDialog('set_appearance', VAULT);
  component.selectColor('text-warning');
  component.selectIcon('briefcase');
  await component.saveVaultDialog();
  assert.equal(bodies.length, 1);
  assert.equal(bodies[0].color, 'warning', 'the class prefix never reaches the server');
  assert.equal(bodies[0].icon, 'briefcase');
  assert.equal(bodies[0].name, 'Personal');
  assert.equal(component.vaultDialog, null, 'a saved dialog closes');
  assert.equal(component.loaded, 1);
});

test('a blank name is never written', async () => {
  const { component, calls } = switcher();
  component.openVaultDialog('rename', VAULT);
  component.vaultDialog.name = '   ';
  await component.saveVaultDialog();
  assert.deepStrictEqual(Array.from(calls), []);
  assert.notEqual(component.vaultDialog, null, 'the dialog stays open to be corrected');
});

// --- deleting, including the one you are standing in ------------------------

test('deleting the open vault moves on rather than reporting it missing', async () => {
  // The vault under your feet is gone, but nothing is out of reach: dropping
  // the pointer first is what sends the reload back through the landing
  // resolution instead of looking up a row nobody will find.
  const { component, calls } = switcher();
  await component.loadVaultActions();
  await component.runVaultAction(ACTION.delete, VAULT);
  assert.deepStrictEqual(Array.from(calls), ['delete:v-1']);
  assert.equal(component.vaultUuid, null, 'the pointer is dropped before the reload');
  assert.equal(component.loaded, 1);
});

test('deleting a vault you are not in leaves the open one alone', async () => {
  const { component } = switcher();
  await component.loadVaultActions();
  await component.runVaultAction(ACTION.delete, OTHER);
  assert.equal(component.vaultUuid, 'v-1');
  assert.equal(component.loaded, 1);
});

test('a refused confirmation deletes nothing', async () => {
  const { component, calls } = switcher({ component: { confirm: async () => false } });
  await component.loadVaultActions();
  await component.runVaultAction(ACTION.delete, VAULT);
  assert.deepStrictEqual(Array.from(calls), []);
});

// --- the lock ---------------------------------------------------------------

test('a lock closes the switcher and drops what it held', () => {
  const { component } = switcher();
  component.switcherOpen = true;
  component.openVaultDialog('rename', VAULT);
  component.vaultActions = { 'v-1': EVERY_ACTION };
  component.onSwitcherLocked();
  assert.equal(component.switcherOpen, false);
  assert.equal(component.vaultDialog, null);
  assert.equal(component.newVault, null);
  assert.deepStrictEqual({ ...component.vaultActions }, {});
  assert.equal(component.vaultMenu.open, false);
});

// --- creation, including the race the endpoint answers 409 to ---------------

test('a created vault is the one you end up in', async () => {
  const { component } = switcher();
  component.openCreateDialog();
  component.newVault.name = 'Cards';
  await component.createVault();
  assert.equal(component.vaultUuid, 'v-new');
  assert.equal(component.newVault, null, 'the dialog closes behind it');
});

test('a vault recovered from a 409 is opened like any other', async () => {
  // The answer was lost, not the write: the vault exists. Confirming that and
  // then leaving the user where they were is the thing the happy path calls
  // wondering where it went.
  let attempts = 0;
  const { component } = switcher({
    api: {
      createVault: async () => {
        attempts += 1;
        const err = new Error('taken');
        err.status = 409;
        throw err;
      },
    },
  });
  component.vaults = [VAULT, OTHER, { uuid: 'v-new', name: 'Cards' }];
  component.openCreateDialog();
  component.newVault.name = 'Cards';
  await component.createVault();
  assert.equal(attempts, 1);
  assert.equal(component.newVault, null, 'the dialog closes: the vault is there');
  assert.equal(component.vaultUuid, 'v-new');
  assert.equal(component.error, '');
});

test('a 409 for a uuid that is nobody else here reports the failure', async () => {
  const { component } = switcher({
    api: {
      createVault: async () => {
        const err = new Error('taken');
        err.status = 409;
        throw err;
      },
    },
  });
  component.openCreateDialog();
  component.newVault.name = 'Cards';
  await component.createVault();
  assert.match(component.error, /could not be created/);
  assert.equal(component.vaultUuid, 'v-1', 'and leaves the open vault alone');
});
