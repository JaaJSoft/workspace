const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// Each fetch call is captured as a deferred so tests control completion
// order - the concurrency-guard races below depend on it.
function deferredFetch() {
  const calls = [];
  const impl = (url, opts) =>
    new Promise((resolve, reject) => calls.push({ url, opts, resolve, reject }));
  return { impl, calls };
}

function load(fetchImpl) {
  return loadScript('workspace/people/ui/static/people/ui/js/people.js', {
    document: { getElementById: () => null },
    sidebarPreference: { initial: () => false, save: () => {} },
    matchMedia: () => ({ matches: false }),
    history: { replaceState() {}, pushState() {}, state: null },
    URLSearchParams,
    fetch: fetchImpl || (() => Promise.reject(new Error('no network in tests'))),
    getCSRFToken: () => 'token',
    AppAlert: { show() {} },
  });
}

test('groupPersons groups by first letter and buckets the rest under #', () => {
  const ctx = load();
  const groups = Array.from(
    ctx.peopleHelpers.groupPersons([
      { display_name: 'alice' },
      { display_name: 'Bob' },
      { display_name: '42 crew' },
      { display_name: 'Anna' },
    ])
  ).map((g) => ({ letter: g.letter, names: Array.from(g.items).map((p) => p.display_name) }));
  assert.deepStrictEqual(groups, [
    { letter: 'A', names: ['alice', 'Anna'] },
    { letter: 'B', names: ['Bob'] },
    { letter: '#', names: ['42 crew'] },
  ]);
});

test('listUrl only carries the filters that are set', () => {
  const ctx = load();
  assert.equal(ctx.peopleHelpers.listUrl('/people', {}), '/people');
  assert.equal(
    ctx.peopleHelpers.listUrl('/people', { query: 'bo b', scope: 'mine' }),
    '/people?q=bo+b&scope=mine'
  );
  assert.equal(ctx.peopleHelpers.listUrl('/people', { listUuid: 'abc' }), '/people?list=abc');
});

test('setScope clears the list filter and setList clears the scope', () => {
  const ctx = load();
  const app = ctx.peopleApp({ scope: 'mine', listUuid: 'abc' });
  app.$ajax = () => Promise.resolve();
  app.setScope('group:1');
  assert.equal(app.scope, 'group:1');
  assert.equal(app.listUuid, '');
  app.setList('xyz');
  assert.equal(app.listUuid, 'xyz');
  assert.equal(app.scope, '');
});

test('personPanel.can reads the action list', () => {
  const ctx = load();
  const panel = ctx.personPanel();
  panel.actions = [{ id: 'edit' }, { id: 'delete' }];
  assert.equal(panel.can('edit'), true);
  assert.equal(panel.can('move'), false);
  assert.deepStrictEqual(
    Array.from(panel.menuActions()).map((a) => a.id),
    ['delete']
  );
});

// The regression a previous task fixed by hand: a PATCH reply must not
// clobber a local edit that happened while the request was in flight.
test('patch keeps an in-flight local edit when the reply lands after it', async () => {
  const { impl, calls } = deferredFetch();
  const ctx = load(impl);
  const panel = ctx.personPanel();
  panel.person = { uuid: 'p1', emails: [{ value: 'a@x.io', type: 'home' }], display_name: 'A' };
  panel.actions = [{ id: 'edit' }];

  const patchPromise = panel.patch({ emails: panel.person.emails });
  // The click that started this PATCH already ran; the row it added must survive.
  panel.person.emails.push({ value: '', type: 'home' });
  calls[0].resolve({ ok: true, json: async () => ({ uuid: 'p1', emails: [{ value: 'a@x.io', type: 'home' }] }) });
  await patchPromise;

  assert.equal(panel.person.emails.length, 2);
});

test('patch adopts the reply when nothing changed locally in the meantime', async () => {
  const { impl, calls } = deferredFetch();
  const ctx = load(impl);
  const panel = ctx.personPanel();
  panel.person = { uuid: 'p1', emails: [{ value: 'a@x.io', type: 'home' }], display_name: 'A' };
  panel.actions = [{ id: 'edit' }];

  const patchPromise = panel.patch({ emails: panel.person.emails });
  calls[0].resolve({ ok: true, json: async () => ({ uuid: 'p1', emails: [{ value: 'a@x.io', type: 'personal' }] }) });
  await patchPromise;

  assert.deepStrictEqual(panel.person.emails, [{ value: 'a@x.io', type: 'personal' }]);
});

// Isolates the generation counter from the "before" snapshot check: B's
// reply happens to put `emails` back to the same JSON it held when A's
// request went out, so the before-check alone would wrongly wave A's stale
// reply through. Only the generation mismatch (_patchGen[key] !== claim.gen)
// catches it.
test('a stale reply is dropped when a newer patch of the same key already landed', async () => {
  const { impl, calls } = deferredFetch();
  const ctx = load(impl);
  const panel = ctx.personPanel();
  panel.person = { uuid: 'p1', emails: [{ value: 'a@x.io', type: 'home' }], display_name: 'A' };
  panel.actions = [{ id: 'edit' }];

  const first = panel.patch({ emails: [{ value: 'a-stale-request', type: 'home' }] }); // gen 1, resolves last
  const second = panel.patch({ emails: [{ value: 'b-new-request', type: 'home' }] }); // gen 2, resolves first

  // B's reply happens to restore the exact JSON `emails` held before A was sent.
  calls[1].resolve({ ok: true, json: async () => ({ uuid: 'p1', emails: [{ value: 'a@x.io', type: 'home' }] }) });
  await second;
  calls[0].resolve({ ok: true, json: async () => ({ uuid: 'p1', emails: [{ value: 'a-stale-reply', type: 'home' }] }) });
  await first;

  assert.deepStrictEqual(panel.person.emails, [{ value: 'a@x.io', type: 'home' }]);
});
