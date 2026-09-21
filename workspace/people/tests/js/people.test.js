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
    FormData,
    fetch: fetchImpl || (() => Promise.reject(new Error('no network in tests'))),
    getCSRFToken: () => 'token',
    AppAlert: { show() {} },
  });
}

test('listUrl only carries the filters that are set', () => {
  const ctx = load();
  assert.equal(ctx.peopleHelpers.listUrl('/people', {}), '/people');
  assert.equal(
    ctx.peopleHelpers.listUrl('/people', { query: 'bo b', scope: 'mine' }),
    '/people?q=bo+b&scope=mine'
  );
  assert.equal(ctx.peopleHelpers.listUrl('/people', { listUuid: 'abc' }), '/people?list=abc');
});

test('exportUrl picks the list, then the scope, then everything', () => {
  const ctx = load();
  assert.equal(ctx.peopleHelpers.exportUrl({}), '/api/v1/people/export');
  assert.equal(
    ctx.peopleHelpers.exportUrl({ scope: 'group:3' }),
    '/api/v1/people/export?scope=group%3A3'
  );
  assert.equal(
    ctx.peopleHelpers.exportUrl({ scope: 'mine', listUuid: 'abc' }),
    '/api/v1/people/lists/abc/vcf'
  );
});

test('the export dialog opens on the sidebar selection and counts what it covers', () => {
  const ctx = load();
  const app = ctx.peopleApp({ scope: 'group:3' });
  app.mineCount = 4;
  app.groups = [{ id: 3, name: 'Team', person_count: 2 }];
  app.lists = [
    { uuid: 'l1', name: 'Friends', member_count: 1, scope: 'mine' },
    { uuid: 'l2', name: 'Crew', member_count: 2, scope: 'group:3' },
  ];
  let opened = false;
  app.$refs = { exportDialog: { showModal: () => { opened = true; }, close() {} } };
  app.openExport();
  assert.equal(opened, true);
  assert.equal(app.exportForm.target, 'group:3');
  assert.deepEqual({ ...app.exportSummary() }, { contacts: 2, lists: 1 });
  app.exportForm.target = 'all';
  assert.deepEqual({ ...app.exportSummary() }, { contacts: 6, lists: 2 });
  assert.equal(app.exportSummaryText(), '6 contacts and 2 lists will be exported.');
  app.exportForm.target = 'list:l1';
  assert.equal(app.exportSummaryText(), '1 contact and 1 list will be exported.');
  app.listUuid = 'l2';
  app.openExport();
  assert.equal(app.exportForm.target, 'list:l2');
  app.onScopeChanged({ from: null, to: 'mine' });
  assert.equal(app.mineCount, 5);
});

test('exportHref follows the chosen target', () => {
  const ctx = load();
  const app = ctx.peopleApp({});
  app.exportForm.target = 'list:abc';
  assert.equal(app.exportHref(), '/api/v1/people/lists/abc/vcf');
  app.exportForm.target = 'all';
  assert.equal(app.exportHref(), '/api/v1/people/export');
  app.exportForm.target = 'group:3';
  assert.equal(app.exportHref(), '/api/v1/people/export?scope=group%3A3');
});

test('runImport posts the file, shows the report and refreshes the sidebar', async () => {
  const { impl, calls } = deferredFetch();
  const ctx = load(impl);
  const app = ctx.peopleApp({ scope: 'group:3' });
  app.groups = [{ id: 3, name: 'Team', person_count: 1 }];
  app.$refs = { importFile: { value: 'x' } };
  app.$ajax = () => Promise.resolve();
  app.resetImportForm();
  assert.equal(app.importForm.scope, 'group:3');
  app.importForm.file = { name: 'a.vcf' };
  const done = app.runImport();
  assert.equal(calls[0].url, '/api/v1/people/import');
  assert.equal(calls[0].opts.method, 'POST');
  calls[0].resolve({
    ok: true,
    json: () => Promise.resolve({ created: 2, updated: 1, lists: 1 }),
  });
  await new Promise((r) => setImmediate(r));
  // reloadLists is the second request.
  assert.equal(calls[1].url, '/api/v1/people/lists');
  calls[1].resolve({ ok: true, json: () => Promise.resolve([]) });
  await done;
  assert.deepEqual({ ...app.importResult }, { created: 2, updated: 1, lists: 1 });
  assert.equal(app.groups[0].person_count, 3);
  assert.equal(app.saving, false);
});

test('runImport surfaces the server error inside the dialog', async () => {
  const { impl, calls } = deferredFetch();
  const ctx = load(impl);
  const app = ctx.peopleApp({});
  app.$refs = {};
  app.resetImportForm();
  app.importForm.file = { name: 'a.txt' };
  const done = app.runImport();
  calls[0].resolve({
    ok: false,
    json: () => Promise.resolve({ file: ['This is not a vCard file.'] }),
  });
  await done;
  assert.equal(app.importError, 'This is not a vCard file.');
  assert.equal(app.importResult, null);
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

test('the sidebar lists only the groups that hold contacts and follows moves', () => {
  const ctx = load();
  const app = ctx.peopleApp({});
  app.groups = [
    { id: 1, name: 'Design', person_count: 2 },
    { id: 2, name: 'Empty', person_count: 0 },
  ];
  assert.deepStrictEqual(Array.from(app.visibleGroups()).map((g) => g.name), ['Design']);
  app.onScopeChanged({ from: 'mine', to: 'group:2' });
  assert.deepStrictEqual(Array.from(app.visibleGroups()).map((g) => g.name), ['Design', 'Empty']);
  app.onScopeChanged({ from: 'group:1', to: 'group:1' });
  app.onScopeChanged({ from: 'group:1', to: null });
  app.onScopeChanged({ from: 'group:1', to: 'mine' });
  assert.deepStrictEqual(Array.from(app.visibleGroups()).map((g) => g.name), ['Empty']);
  // An unknown group is ignored rather than crashing the page.
  app.onScopeChanged({ from: 'group:99', to: 'group:98' });
});

test('the person context menu opens on the row and fills from the actions endpoint', async () => {
  const { impl, calls } = deferredFetch();
  const ctx = load(impl);
  const app = ctx.peopleApp({});
  app.$nextTick = (fn) => fn();
  app.$el = { querySelector: () => null };
  const event = { preventDefault() {}, clientX: 40, clientY: 50 };
  app.openCtxMenu(event, 'person', { uuid: 'p1', name: 'Bob', scope: 'mine' });
  assert.equal(app.ctxMenu.open, true);
  assert.equal(app.ctxMenu.type, 'person');
  assert.equal(app.ctxMenu.actions, null);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/v1/people/actions');
  calls[0].resolve({ ok: true, json: async () => ({ p1: [{ id: 'edit' }, { id: 'delete' }] }) });
  await new Promise((r) => setTimeout(r, 0));
  assert.deepStrictEqual(Array.from(app.ctxMenu.actions).map((a) => a.id), ['edit', 'delete']);
  // The menu never lists `edit`: it is the gate of the inline fields.
  assert.deepStrictEqual(Array.from(app.ctxMenuRows()).map((a) => a.id), ['delete']);
});

test('a menu closed before its actions land ignores the late answer', async () => {
  const { impl, calls } = deferredFetch();
  const ctx = load(impl);
  const app = ctx.peopleApp({});
  app.$nextTick = (fn) => fn();
  app.$el = { querySelector: () => null };
  const event = { preventDefault() {}, clientX: 0, clientY: 0 };
  app.openCtxMenu(event, 'person', { uuid: 'p1', name: 'Bob', scope: 'mine' });
  app.closeCtxMenu();
  calls[0].resolve({ ok: true, json: async () => ({ p1: [{ id: 'edit' }] }) });
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(app.ctxMenu.open, false);
  assert.equal(app.ctxMenu.actions, null);
});

test('a list context menu needs no request and routes rename and delete', () => {
  const ctx = load();
  const app = ctx.peopleApp({});
  app.$nextTick = (fn) => fn();
  app.$el = { querySelector: () => null };
  const calls = [];
  app.renameList = (list) => calls.push(['rename', list.uuid]);
  app.deleteList = (list) => calls.push(['delete', list.uuid]);
  app.openCtxMenu({ preventDefault() {}, clientX: 0, clientY: 0 }, 'list', { uuid: 'l1', name: 'Family' });
  assert.deepStrictEqual(Array.from(app.ctxMenu.actions), []);
  app.ctxListAction('rename');
  assert.equal(app.ctxMenu.open, false);
  app.openCtxMenu({ preventDefault() {}, clientX: 0, clientY: 0 }, 'list', { uuid: 'l1', name: 'Family' });
  app.ctxListAction('delete');
  assert.deepStrictEqual(calls, [['rename', 'l1'], ['delete', 'l1']]);
});
