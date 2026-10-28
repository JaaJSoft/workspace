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

// Records every write, so a test can assert which entries a gesture adds.
function navStub({ returnsBack = false } = {}) {
  const writes = [];
  return {
    writes,
    push: (url, state) => writes.push(['push', url, state]),
    replace: (url, state) => writes.push(['replace', url, state]),
    returnTo: (url) => {
      writes.push(['returnTo', url]);
      return returnsBack;
    },
    state: () => ({}),
  };
}

function load(fetchImpl, extra = {}) {
  return loadScript('workspace/people/ui/static/people/ui/js/people.js', {
    document: { getElementById: () => null },
    sidebarPreference: { initial: () => false, save: () => {} },
    matchMedia: () => ({ matches: false }),
    navHistory: navStub(),
    URLSearchParams,
    FormData,
    fetch: fetchImpl || (() => Promise.reject(new Error('no network in tests'))),
    getCSRFToken: () => 'token',
    AppAlert: { show() {} },
    ...extra,
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

test('qrUrl asks for svg by default and names the kind otherwise', () => {
  const ctx = load();
  const url = ctx.peopleHelpers.qrUrl;
  assert.equal(url('abc'), '/api/v1/people/qrcode?person=abc');
  assert.equal(url('abc', 'svg'), '/api/v1/people/qrcode?person=abc');
  assert.equal(url('abc', 'png'), '/api/v1/people/qrcode?person=abc&kind=png');
});

test('exportUrl maps every target onto the one export endpoint', () => {
  const ctx = load();
  const url = ctx.peopleHelpers.exportUrl;
  assert.equal(url('all'), '/api/v1/people/export');
  assert.equal(url('mine'), '/api/v1/people/export?scope=mine');
  assert.equal(url('group:3'), '/api/v1/people/export?scope=group%3A3');
  assert.equal(url('list:abc'), '/api/v1/people/export?list=abc');
  assert.equal(url('person:a-b'), '/api/v1/people/export?person=a-b');
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

test('the export summary is one translated sentence with translated plurals', () => {
  const catalog = {
    '%(contacts)s and %(lists)s will be exported.': 'À exporter : %(contacts)s et %(lists)s.',
  };
  const plurals = {
    '%(count)s contact': ['%(count)s contact', '%(count)s contacts'],
    '%(count)s list': ['%(count)s liste', '%(count)s listes'],
  };
  const ctx = load(null, {
    gettext: (msgid) => catalog[msgid] || msgid,
    // French puts 0 with 1 in the singular form.
    ngettext: (singular, plural, count) => plurals[singular][count <= 1 ? 0 : 1],
  });
  const app = ctx.peopleApp({});
  app.mineCount = 3;
  app.lists = [];
  app.exportForm.target = 'mine';
  assert.equal(app.exportSummaryText(), 'À exporter : 3 contacts et 0 liste.');
});

test('menu rows open the export dialog on their target instead of downloading', () => {
  const ctx = load();
  const app = ctx.peopleApp({ scope: 'mine' });
  let opened = 0;
  app.$refs = { exportDialog: { showModal: () => { opened += 1; }, close() {} } };
  app.ctxMenu = { open: true, type: 'person', data: { uuid: 'p1', name: 'Jane', scope: 'mine' } };
  app.ctxPersonAction({ id: 'export' });
  assert.equal(opened, 1);
  assert.equal(app.exportForm.target, 'person:p1');
  assert.equal(app.exportPerson.name, 'Jane');
  assert.deepEqual({ ...app.exportSummary() }, { contacts: 1, lists: 0 });
  assert.equal(app.exportHref(), '/api/v1/people/export?person=p1');
  app.ctxMenu = { open: true, type: 'list', data: { uuid: 'l1', name: 'Friends' } };
  app.ctxListAction('export');
  assert.equal(opened, 2);
  assert.equal(app.exportForm.target, 'list:l1');
  assert.equal(app.exportPerson, null);
  assert.equal(app.exportHref(), '/api/v1/people/export?list=l1');
  assert.equal(typeof ctx.window.location, 'undefined');
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

test('a QR image failure says too large only when the server answered 413', async () => {
  const cases = [
    [() => Promise.resolve({ status: 413 }), 'too_large'],
    [() => Promise.resolve({ status: 500 }), 'failed'],
    [() => Promise.resolve({ status: 403 }), 'failed'],
    [() => Promise.reject(new Error('offline')), 'failed'],
  ];
  for (const [fetchImpl, expected] of cases) {
    const requested = [];
    const ctx = load((url) => {
      requested.push(url);
      return fetchImpl();
    });
    const app = ctx.peopleApp({});
    app.$refs = { qrDialog: { showModal() {} } };
    app.openQrCode({ uuid: 'abc', name: 'Alice' });
    assert.equal(app.qrError, '');
    await app.qrLoadFailed();
    assert.equal(app.qrError, expected);
    assert.deepEqual(requested, ['/api/v1/people/qrcode?person=abc']);
  }
});

test('opening a contact from the list pushes, switching to another replaces', () => {
  const nav = navStub();
  const ctx = load(undefined, { navHistory: nav });
  const app = ctx.peopleApp({ query: 'bo' });
  const panels = [];
  app.$ajax = (url) => {
    panels.push(url);
    return Promise.resolve();
  };
  app.openPerson('p1');
  app.openPerson('p2');
  assert.deepEqual(
    nav.writes.map((w) => [w[0], w[1]]),
    [
      ['push', '/people?q=bo&person=p1'],
      ['replace', '/people?q=bo&person=p2'],
    ]
  );
  assert.deepEqual(panels, ['/people/p1/panel', '/people/p2/panel']);
});

test('closing the panel returns to the list instead of adding an entry', () => {
  const nav = navStub();
  const ctx = load(undefined, { navHistory: nav });
  const app = ctx.peopleApp({ scope: 'mine', initialPerson: 'p1' });
  app.$ajax = () => Promise.resolve();
  app.closePanel();
  assert.deepEqual(nav.writes, [['returnTo', '/people?scope=mine']]);
  assert.equal(app.panelOpen, false);
  assert.equal(app.current, '');
});

test('a deep link loads its contact without writing history', () => {
  const nav = navStub();
  const ctx = load(undefined, { navHistory: nav, location: { search: '?person=p1' } });
  const app = ctx.peopleApp({ initialPerson: 'p1' });
  const panels = [];
  app.$ajax = (url) => {
    panels.push(url);
    return Promise.resolve();
  };
  app.init();
  assert.deepEqual(nav.writes, []);
  assert.deepEqual(panels, ['/people/p1/panel']);
});

test('filters rewrite the entry on screen', () => {
  const nav = navStub();
  const ctx = load(undefined, { navHistory: nav });
  const app = ctx.peopleApp({});
  app.$ajax = () => Promise.resolve();
  app.query = 'al';
  app.refreshList();
  app.setScope('mine');
  assert.deepEqual(
    nav.writes.map((w) => w[0]),
    ['replace', 'replace']
  );
});

function restoreEvent(stale) {
  const event = { detail: { state: {}, stale }, prevented: false };
  event.preventDefault = () => {
    event.prevented = true;
  };
  return event;
}

test('a restore follows the URL and never writes history', () => {
  const nav = navStub();
  const location = { search: '?q=al&person=p2' };
  const ctx = load(undefined, { navHistory: nav, location });
  const app = ctx.peopleApp({ query: 'al', initialPerson: 'p1' });
  const swaps = [];
  app.$ajax = (url, opts) => {
    swaps.push([url, opts.target]);
    return Promise.resolve();
  };

  const toOther = restoreEvent(false);
  app.onRestore(toOther);
  assert.equal(app.current, 'p2');
  assert.equal(app.panelOpen, true);
  assert.deepEqual(swaps, [['/people/p2/panel', 'person-panel']]);
  assert.equal(toOther.prevented, false);

  location.search = '?q=al';
  app.onRestore(restoreEvent(false));
  assert.equal(app.panelOpen, false);
  assert.equal(app.current, '');
  assert.equal(swaps.length, 1);

  location.search = '?q=bob';
  app.onRestore(restoreEvent(false));
  assert.equal(app.query, 'bob');
  assert.deepEqual(swaps[1], ['/people?q=bob', 'person-list']);

  assert.deepEqual(nav.writes, []);
});

test('a stale restore refreshes the list and the panel in place', () => {
  const nav = navStub();
  const location = { search: '?person=p1' };
  const ctx = load(undefined, { navHistory: nav, location });
  const app = ctx.peopleApp({ initialPerson: 'p1' });
  const swaps = [];
  app.$ajax = (url) => {
    swaps.push(url);
    return Promise.resolve();
  };
  const event = restoreEvent(true);
  app.onRestore(event);
  assert.equal(event.prevented, true);
  assert.deepEqual(swaps, ['/people', '/people/p1/panel']);
  assert.deepEqual(nav.writes, []);
});
