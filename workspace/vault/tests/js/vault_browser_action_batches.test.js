// POST /api/v1/vault/actions refuses more than 200 UUIDs in one call. A vault
// past that cap used to get a 400 back and no actions at all - every context
// menu empty, no bulk bar, no message, whatever the entry. The fetch has to
// slice, and each slice has to hold the guards on its own.
const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

const VAULT_UUID = '11111111-1111-7111-8111-111111111111';

function browser(options = {}) {
  const ctx = loadScripts(
    [
      'workspace/vault/ui/static/vault/ui/js/vault_format.js',
      'workspace/vault/ui/static/vault/ui/js/vault_menu.js',
      'workspace/vault/ui/static/vault/ui/js/vault_tiles.js',
      'workspace/vault/ui/static/vault/ui/js/vault_prefs.js',
      'workspace/vault/ui/static/vault/ui/js/vault_view_prefs.js',
      'workspace/vault/ui/static/vault/ui/js/vault_unlock.js',
      'workspace/vault/ui/static/vault/ui/js/vault_store.js',
      'workspace/vault/ui/static/vault/ui/js/vault_reader.js',
      'workspace/vault/ui/static/vault/ui/js/entry_write.js',
      'workspace/vault/ui/static/vault/ui/js/folder_write.js',
      'workspace/vault/ui/static/vault/ui/js/tag_write.js',
      'workspace/vault/ui/static/vault/ui/js/clipboard.js',
      'workspace/vault/ui/static/vault/ui/js/vault_resign.js',
      'workspace/vault/ui/static/vault/ui/js/vault_switcher.js',
      'workspace/vault/ui/static/vault/ui/js/vault_browser.js',
    ],
    {
      TextEncoder: globalThis.TextEncoder,
      TextDecoder: globalThis.TextDecoder,
      URLSearchParams: globalThis.URLSearchParams,
      document: {
        getElementById: (id) =>
          id === 'vault-uuid' ? { textContent: JSON.stringify(VAULT_UUID) } : null,
        addEventListener() {},
      },
      location: { search: '' },
      localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
      addEventListener() {},
      history: { replaceState() {} },
      setInterval: () => 1,
      clearInterval() {},
      TAG_CHIP_COLORS: [{ name: 'None', value: '' }],
      vaultApi: { fetchEntryActions: options.fetchEntryActions },
      vaultSession: { isUnlocked: options.isUnlocked || (() => true) },
      vaultClipboard: { cancel() {} },
      vaultCrypto: {},
    },
  );
  const component = ctx.vaultBrowser();
  component.entries = Array.from(
    { length: options.count || 0 },
    (_, i) => ({ uuid: `e-${i}` }),
  );
  return { component, ctx };
}

// The wire shape: one list per UUID submitted. Every UUID submitted comes back
// with a key, empty list included - this endpoint never answers 404.
function answerFor(uuids) {
  return Object.fromEntries(uuids.map((uuid) => [uuid, [{ id: 'edit' }]]));
}

// The value itself is held against the server's MAX_BATCH by
// vault/tests/test_entry_actions.py; asserting the number here would only
// compare a literal to a literal.
test('the browser declares the batch size it slices at', () => {
  const { ctx } = browser({ fetchEntryActions: async () => ({}) });
  assert.equal(typeof ctx.VAULT_ACTIONS_BATCH_SIZE, 'number');
  assert.ok(ctx.VAULT_ACTIONS_BATCH_SIZE > 0);
});

test('a slice landing does not strip rows another answer already filled', async () => {
  // A reload runs on every mutating action - favourite, trash, a folder or
  // tag save - and rebuilds the listing with the same rows. Publishing only
  // what this pass has answered so far would take the actions away from every
  // row past the first slice until its own slice lands, and an open panel on
  // one of those rows would lose its copy buttons mid-refresh: the very
  // symptom this file exists to prevent.
  const resolvers = [];
  const { component } = browser({
    count: 450,
    fetchEntryActions: (uuids) =>
      new Promise((resolve) => {
        resolvers.push(() => resolve(answerFor(uuids)));
      }),
  });
  // What the previous pass left behind, plus a row an opened menu filled in
  // on its own while the slices were in flight.
  component.entryActions = { 'e-400': [{ id: 'edit' }] };

  const done = component.loadEntryActions();
  resolvers[0]();
  await new Promise((resolve) => setImmediate(resolve));

  assert.ok(component.entryActions['e-0'], 'the slice that landed is published');
  assert.ok(
    component.entryActions['e-400'],
    'and a row already answered for keeps its actions',
  );

  resolvers[1]();
  resolvers[2]();
  await done;
  assert.equal(Object.keys(component.entryActions).length, 450);
});

test('a lock mid-flight is not undone by an answer that lands after re-unlock', async () => {
  // onLocked empties the map, and the session is unlocked again by the time
  // the pre-lock answer comes back - so isUnlocked() cannot catch it. Only a
  // generation bump can, which is why onLocked owes one.
  let unlocked = true;
  const resolvers = [];
  const { component } = browser({
    count: 250,
    isUnlocked: () => unlocked,
    fetchEntryActions: (uuids) =>
      new Promise((resolve) => {
        resolvers.push(() => resolve(answerFor(uuids)));
      }),
  });

  const inflight = component.loadEntryActions();
  unlocked = false;
  component.onLocked();
  unlocked = true;
  resolvers.forEach((resolve) => resolve());
  await inflight;

  assert.equal(Object.keys(component.entryActions).length, 0);
});

test('loadEntryActions asks in slices of 200 and merges the answers', async () => {
  const calls = [];
  const { component } = browser({
    count: 450,
    fetchEntryActions: async (uuids) => {
      calls.push(uuids);
      return answerFor(uuids);
    },
  });

  await component.loadEntryActions();

  assert.deepStrictEqual(calls.map((call) => call.length), [200, 200, 50]);
  assert.deepStrictEqual(
    Array.from(calls.flatMap((call) => Array.from(call))),
    component.entries.map((entry) => entry.uuid),
  );
  assert.equal(Object.keys(component.entryActions).length, 450);
  assert.equal(component.entryActions['e-449'][0].id, 'edit');
});

test('a small vault still costs one request', async () => {
  const calls = [];
  const { component } = browser({
    count: 2,
    fetchEntryActions: async (uuids) => {
      calls.push(uuids);
      return answerFor(uuids);
    },
  });

  await component.loadEntryActions();

  assert.deepStrictEqual(calls.map((call) => Array.from(call)), [['e-0', 'e-1']]);
});

test('a slice lost to the network does not cost the others their answers', async () => {
  let call = 0;
  const { component } = browser({
    count: 450,
    fetchEntryActions: async (uuids) => {
      call += 1;
      if (call === 2) throw new TypeError('Failed to fetch');
      return answerFor(uuids);
    },
  });

  await component.loadEntryActions();

  assert.equal(Object.keys(component.entryActions).length, 250);
  assert.ok(component.entryActions['e-0']);
  assert.equal(component.entryActions['e-200'], undefined);
  assert.ok(component.entryActions['e-449']);
});

test('an answer landing after a newer listing started is discarded', async () => {
  // The guard has to hold after every await, not only after the first: with
  // several requests in flight there are several places a stale answer can
  // come back through.
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  let call = 0;
  const { component } = browser({
    count: 250,
    fetchEntryActions: async (uuids) => {
      call += 1;
      if (call === 1) {
        await held;
        return Object.fromEntries(uuids.map((uuid) => [uuid, [{ id: 'stale' }]]));
      }
      return answerFor(uuids);
    },
  });

  const first = component.loadEntryActions();
  component.actionsGeneration += 1;
  release();
  await first;

  assert.equal(Object.keys(component.entryActions).length, 0);
});

test('an idle lock during the round trip keeps the answers off a locked page', async () => {
  // Neither await is atomic with the lock. A timeout firing while the slices
  // are in flight has already emptied the store, and publishing anyway would
  // put a menu back onto a page that is supposed to be shut.
  let locked = false;
  const { component } = browser({
    count: 250,
    isUnlocked: () => !locked,
    fetchEntryActions: async (uuids) => {
      locked = true;
      return answerFor(uuids);
    },
  });

  await component.loadEntryActions();

  assert.equal(Object.keys(component.entryActions).length, 0);
});
