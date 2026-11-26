const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

// What the page embeds with |json_script, as the imports view builds it.
const PAGE_DATA = {
  'import-kinds-data': [
    { kind: 'files', name: 'Files', description: 'Folders and files, with their dates.' },
    { kind: 'contacts', name: 'Contacts', description: 'Address books, with their contact groups as lists.' },
  ],
  'import-job-statuses-data': [
    { value: 'pending', label: 'pending', active: true },
    { value: 'running', label: 'running', active: true },
    { value: 'completed', label: 'completed', active: false },
    { value: 'failed', label: 'failed', active: false },
    { value: 'cancelled', label: 'cancelled', active: false },
  ],
  'import-conflict-policies-data': [
    { value: 'rename', label: 'Keep both (rename the imported one)', summary: 'keep both' },
  ],
};

function app(fetchStub, extraGlobals = {}) {
  const ctx = loadScript('workspace/imports/ui/static/imports/ui/js/imports.js', {
    document: {
      getElementById: (id) => (id in PAGE_DATA ? { textContent: JSON.stringify(PAGE_DATA[id]) } : null),
      addEventListener: () => {},
    },
    getCSRFToken: () => 'x',
    formatFileSize: (b) => `${b} B`,
    fetch: fetchStub || (() => Promise.reject(new Error('no network in tests'))),
    ...extraGlobals,
  });
  const component = ctx.importsApp();
  component.$nextTick = (fn) => fn();
  return { ctx, component };
}

test('progress derives done/total/pct from the files stats', () => {
  const { component } = app();
  const job = { status: 'running', stats: { files: { total_files: 10, files: 3, unchanged: 1, skipped: 1, failed: 0, phase: 'copying' } } };
  const p = component.progress(job);
  assert.equal(p.done, 5);
  assert.equal(p.total, 10);
  assert.equal(p.pct, 50);
  assert.equal(component.phaseLabel(job), 'Copying… 5 / 10');
});

test('a completed job is 100% even without totals', () => {
  const { component } = app();
  assert.equal(component.progress({ status: 'completed', stats: {} }).pct, 100);
});

test('summary lists the non-zero counters', () => {
  const { component } = app();
  const job = { status: 'completed', stats: { files: { files: 2, failed: 1, bytes: 2048 } } };
  assert.equal(component.summary(job), '2 imported · 1 failed · 2048 B');
});

test('browse crumbs split the remote path', () => {
  const { component } = app();
  component.browse.path = '/Documents/2024';
  const crumbs = Array.from(component.browseCrumbs()).map((c) => ({ ...c }));
  assert.deepStrictEqual(crumbs, [
    { label: 'Root', path: '/' },
    { label: 'Documents', path: '/Documents' },
    { label: '2024', path: '/Documents/2024' },
  ]);
});

test('errorMessage flattens DRF payloads', () => {
  const { ctx } = app();
  assert.equal(ctx.errorMessage({ detail: 'nope' }, 'x'), 'nope');
  assert.equal(ctx.errorMessage({ base_url: ['Enter a valid URL.'] }, 'x'), 'base_url: Enter a valid URL.');
  assert.equal(ctx.errorMessage({ options: { files: { on_conflict: ['bad'] } } }, 'x'), 'options.files.on_conflict: bad');
  assert.equal(ctx.errorMessage(null, 'fallback'), 'fallback');
});

test('refreshJob inserts an unknown job and updates a known one in place', async () => {
  const calls = [];
  const fetchStub = (url) => {
    calls.push(url);
    const uuid = url.split('/').pop();
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ uuid, status: 'completed' }) });
  };
  const { component } = app(fetchStub);
  component.jobs = [{ uuid: 'a', status: 'running' }, { uuid: 'b', status: 'running' }];
  await component.onJobEvent({ job: 'b' });
  assert.deepStrictEqual(component.jobs.map((j) => `${j.uuid}:${j.status}`), ['a:running', 'b:completed']);
  await component.onJobEvent({ job: 'c' });
  assert.deepStrictEqual(component.jobs.map((j) => j.uuid), ['c', 'a', 'b']);
  assert.deepStrictEqual(calls, ['/api/v1/imports/jobs/b', '/api/v1/imports/jobs/c']);
});

test('wizard kinds come from the selected connection provider', () => {
  const { component } = app();
  component.providers = [{ slug: 'fake', kinds: ['files', 'photos'] }];
  component.wizard.connection = { provider: 'fake' };
  const kinds = Array.from(component.wizardKinds()).map((k) => ({ ...k }));
  assert.deepStrictEqual(kinds.map((k) => k.kind), ['files', 'photos']);
  assert.equal(kinds[0].name, 'Files');
  assert.equal(kinds[1].name, 'photos');
  component.toggleKind('photos');
  assert.deepStrictEqual(Array.from(component.wizard.kinds), ['files', 'photos']);
  component.toggleKind('files');
  assert.deepStrictEqual(Array.from(component.wizard.kinds), ['photos']);
});

test('wizard kinds put files before contacts regardless of provider order', () => {
  const { component } = app();
  component.providers = [{ slug: 'fake', kinds: ['contacts', 'files', 'photos'] }];
  component.wizard.connection = { provider: 'fake' };
  const kinds = Array.from(component.wizardKinds()).map((k) => k.kind);
  assert.deepStrictEqual(kinds, ['files', 'contacts', 'photos']);
});

test('editing a connection only sends the fields that changed', () => {
  const { ctx } = app();
  const original = { label: 'Old', base_url: 'https://a', username: 'me' };
  assert.deepStrictEqual(
    { ...ctx.connectionChanges(original, { label: 'New', base_url: 'https://a', username: 'me', secret: '' }) },
    { label: 'New' },
  );
  assert.deepStrictEqual(
    { ...ctx.connectionChanges(original, { label: 'Old', base_url: 'https://b', username: 'me', secret: 'pw' }) },
    { base_url: 'https://b', secret: 'pw' },
  );
});

test('a requested stop shows as stopping until the worker ends the job', () => {
  const { component } = app();
  const job = { status: 'running', cancel_requested_at: '2026-08-19T10:00:00Z', stats: { files: { total_files: 10, files: 3, phase: 'copying' } } };
  assert.equal(component.isStopping(job), true);
  assert.match(component.phaseLabel(job), /^Stopping/);
  assert.equal(component.isStopping({ status: 'cancelled', cancel_requested_at: '2026-08-19T10:00:00Z' }), false);
  assert.equal(component.isStopping({ status: 'running', cancel_requested_at: null }), false);
});

test('the bar is indeterminate while listing, determinate once copying', () => {
  const { component } = app();
  const listing = { status: 'running', stats: { files: { phase: 'listing', total_files: 400, unchanged: 398 } } };
  assert.equal(component.progress(listing).determinate, false);
  const copying = { status: 'running', stats: { files: { phase: 'copying', total_files: 400, unchanged: 398 } } };
  assert.equal(component.progress(copying).determinate, true);
  assert.equal(component.progress(copying).pct, 100);
  assert.equal(component.progress({ status: 'pending', stats: {} }).determinate, false);
});

test('a retry response does not duplicate a card an SSE refetch already inserted', async () => {
  const fetchStub = (url, opts) => {
    if (opts && opts.method === 'POST') {
      return Promise.resolve({ ok: true, status: 201, json: async () => ({ uuid: 'new', status: 'pending', stats: {} }) });
    }
    return Promise.resolve({ ok: true, status: 200, json: async () => ({ uuid: 'new', status: 'completed', stats: {} }) });
  };
  const { ctx, component } = app(fetchStub);
  ctx.AppAlert = { success() {}, error() {} };
  component.jobs = [{ uuid: 'old', status: 'failed' }];
  // The SSE event beat the retry response: the new job is already listed.
  await component.onJobEvent({ job: 'new' });
  await component.retryJob({ uuid: 'old' });
  assert.deepStrictEqual(component.jobs.map((j) => j.uuid), ['new', 'old']);
  assert.equal(component.jobs[0].status, 'completed');
});

test('progress follows the kind the job is working on', () => {
  const { component } = app();
  const job = {
    status: 'running',
    kinds: ['files', 'contacts'],
    stats: {
      files: { phase: 'done', total_files: 3, files: 3 },
      contacts: { phase: 'importing', total_cards: 10, cards: 3, unchanged: 1, failed: 1 },
    },
  };
  const p = component.progress(job);
  assert.equal(p.kind, 'contacts');
  assert.equal(p.done, 5);
  assert.equal(p.pct, 50);
  assert.equal(component.phaseLabel(job), 'Importing contacts… 5 / 10');
  job.stats.contacts.phase = 'listing';
  assert.equal(component.phaseLabel(job), 'Listing the address books… 10 contacts found');
});

test('summary names each kind unless the job only moved files', () => {
  const { component } = app();
  assert.equal(
    component.summary({
      status: 'completed',
      kinds: ['files', 'contacts'],
      stats: { files: { files: 2 }, contacts: { created: 3, updated: 1, lists: 2 } },
    }),
    'Files: 2 imported / Contacts: 3 added · 1 updated · 2 lists',
  );
  assert.equal(
    component.summary({ status: 'completed', kinds: ['contacts'], stats: { contacts: { updated: 1, unchanged: 4 } } }),
    'Contacts: 1 updated · 4 unchanged',
  );
});

test('failedCount adds up every kind', () => {
  const { component } = app();
  assert.equal(component.failedCount({ stats: { files: { failed: 2 }, contacts: { failed: 1 } } }), 3);
  assert.equal(component.failedCount({ stats: {} }), 0);
});

test('bookSummary and targetLabel read the job and the groups', () => {
  const { component } = app();
  component.groups = [{ id: 4, name: 'Team' }];
  assert.equal(component.bookSummary({ options: { contacts: { books: [{ id: '/a' }] } } }), '1 address book');
  assert.equal(component.bookSummary({ options: { contacts: { books: [{ id: '/a' }, { id: '/b' }] } } }), '2 address books');
  assert.equal(component.targetLabel('mine'), 'My contacts');
  assert.equal(component.targetLabel('group:4'), 'Team');
});

test('the address books step needs at least one book picked', () => {
  const { component } = app();
  component.wizard.step = 3;
  component.wizard.kinds = ['contacts'];
  component.wizard.options.contacts.books = [{ id: '/a', name: 'A', selected: false, target: 'mine' }];
  assert.equal(component.canContinue(), false);
  component.wizard.options.contacts.books[0].selected = true;
  assert.equal(component.canContinue(), true);
});

test('loadAddressBooks picks every book into my contacts', async () => {
  const calls = [];
  const fetchStub = (url) => {
    calls.push(url);
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => ({ kind: 'contacts', path: '', entries: [{ id: '/contacts', name: 'Contacts', description: '' }] }),
    });
  };
  const { component } = app(fetchStub);
  component.wizard.connection = { uuid: 'c1' };
  await component.loadAddressBooks();
  assert.deepStrictEqual(calls, ['/api/v1/imports/connections/c1/browse?kind=contacts']);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(component.wizard.options.contacts.books)), [
    { id: '/contacts', name: 'Contacts', description: '', selected: true, target: 'mine' },
  ]);
  // Coming back to the step keeps the choices instead of listing again.
  await component.loadAddressBooks();
  assert.equal(calls.length, 1);
});

test('launchBody sends the picked address books and only the chosen kinds', () => {
  const { component } = app();
  component.wizard.connection = { uuid: 'c1' };
  component.wizard.kinds = ['contacts'];
  component.wizard.options.contacts.books = [
    { id: '/contacts', name: 'Contacts', selected: true, target: 'mine' },
    { id: '/old', name: 'Old', selected: false, target: 'mine' },
    { id: '/team', name: 'Team', selected: true, target: 'group:4' },
  ];
  assert.deepStrictEqual(JSON.parse(JSON.stringify(component.launchBody())), {
    connection: 'c1',
    kinds: ['contacts'],
    options: {
      contacts: { books: [{ id: '/contacts', target: 'mine' }, { id: '/team', target: 'group:4' }] },
    },
  });
});

test('favorites and tags the remote could not give are reported beside the summary', () => {
  const { component } = app();
  const failed = { status: 'completed', stats: { files: { files: 2, metadata_error: 'The server refused.' } } };
  const clean = { status: 'completed', stats: { files: { files: 2 } } };
  assert.equal(component.metadataError(failed), 'Favorites and tags were not imported: The server refused.');
  assert.equal(component.metadataError(clean), '');
});

test('status and conflict labels come from the page, unknown values as sent', () => {
  const { component } = app();
  assert.equal(component.statusLabel('running'), 'running');
  assert.equal(component.statusLabel('paused'), 'paused');
  assert.equal(component.conflictLabel('rename'), 'keep both');
  assert.equal(component.conflictLabel('merge'), 'merge');
  assert.equal(component.isActive({ status: 'pending' }), true);
  assert.equal(component.isActive({ status: 'failed' }), false);
  assert.equal(component.isActive({ status: 'paused' }), false);
});

// A page with a Nextcloud-like provider offering the sign-in flow, a scripted
// API, a fake tab and timers the test fires by hand.
function signInApp(answers) {
  const requests = [];
  const timers = [];
  const tabs = [];
  const alerts = [];
  const fetchStub = (url, options) => {
    const method = (options && options.method) || 'GET';
    requests.push(`${method} ${url}`);
    const [status, data] = answers.shift();
    return Promise.resolve({ ok: status < 400, status, json: async () => data });
  };
  const { component } = app(fetchStub, {
    open: () => {
      const tab = { opener: 'page', location: { href: '' }, closed: false, close() { this.closed = true; } };
      tabs.push(tab);
      return tab;
    },
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    clearTimeout: () => {},
    AppAlert: { success: (message) => alerts.push(message) },
  });
  component.providers = [
    { slug: 'nextcloud', name: 'Nextcloud', auth: 'credentials', login_flow: true },
    { slug: 'webdav', name: 'WebDAV', auth: 'credentials', login_flow: false },
  ];
  let open = true;
  component.$refs = {
    connectionDialog: { showModal() { open = true; }, close() { open = false; component.onConnectionDialogClosed(); } },
    wizardDialog: { open: false },
  };
  component.openConnectionDialog();
  component.connForm.label = 'Old cloud';
  component.connForm.base_url = 'https://cloud.example.org';
  return { component, requests, timers, tabs, alerts, isOpen: () => open };
}

const PENDING = { uuid: 'f1', status: 'pending', login_url: 'https://cloud.example.org/login/v2/flow/x', error: '', connection: null };

test('a provider with a sign-in flow starts in sign-in mode, others ask for a password', () => {
  const { component } = signInApp([]);
  assert.equal(component.connForm.provider, 'nextcloud');
  assert.equal(component.signingIn(), true);
  assert.equal(component.connectionSubmitLabel(), 'Sign in with Nextcloud');

  component.connForm.provider = 'webdav';
  component.onProviderChange();
  assert.equal(component.signingIn(), false);
  assert.equal(component.connectionSubmitLabel(), 'Connect');

  component.openConnectionDialog({ uuid: 'c1', provider: 'nextcloud', label: 'x', base_url: 'https://a', username: 'me' });
  assert.equal(component.offersSignIn(), false);
});

test('signing in opens the tab first, points it at the sign-in page, then waits', async () => {
  const { component, requests, timers, tabs } = signInApp([[202, PENDING]]);

  await component.saveConnection();

  assert.deepStrictEqual(requests, ['POST /api/v1/imports/login-flows']);
  assert.equal(tabs.length, 1);
  assert.equal(tabs[0].opener, null);
  assert.equal(tabs[0].location.href, PENDING.login_url);
  assert.equal(component.loginFlow.uuid, 'f1');
  assert.equal(timers.length, 1);
});

test('a sign-in the server refuses closes the tab and says why', async () => {
  const { component, tabs, timers } = signInApp([[400, { detail: 'cloud.example.org did not answer.' }]]);

  await component.saveConnection();

  assert.equal(tabs[0].closed, true);
  assert.equal(component.connError, 'cloud.example.org did not answer.');
  assert.equal(component.loginFlow, null);
  assert.equal(timers.length, 0);
});

test('the dialog waits while pending and adds the connection once approved', async () => {
  const connection = { uuid: 'c9', label: 'Old cloud', provider: 'nextcloud' };
  const { component, requests, timers, alerts, isOpen } = signInApp([
    [202, PENDING],
    [200, PENDING],
    [200, { ...PENDING, status: 'completed', connection }],
  ]);
  await component.saveConnection();

  await timers.shift()();
  assert.equal(component.loginFlow.status, 'pending');
  assert.equal(timers.length, 1);

  await timers.shift()();
  assert.deepStrictEqual(Array.from(component.connections, (c) => c.uuid), ['c9']);
  assert.equal(component.loginFlow, null);
  assert.equal(isOpen(), false);
  assert.equal(alerts.length, 1);
  assert.deepStrictEqual(requests, [
    'POST /api/v1/imports/login-flows',
    'GET /api/v1/imports/login-flows/f1',
    'GET /api/v1/imports/login-flows/f1',
  ]);
});

test('a failed sign-in goes back to the form with its reason', async () => {
  const { component, timers } = signInApp([
    [202, PENDING],
    [200, { ...PENDING, status: 'failed', error: 'The sign-in expired. Start it again.' }],
  ]);
  await component.saveConnection();

  await timers.shift()();

  assert.equal(component.loginFlow, null);
  assert.equal(component.connError, 'The sign-in expired. Start it again.');
  assert.equal(timers.length, 0);
});

test('closing the dialog while waiting gives up on the flow', async () => {
  const { component, requests, timers } = signInApp([[202, PENDING], [204, null]]);
  await component.saveConnection();

  component.closeConnectionDialog();

  assert.equal(component.loginFlow, null);
  assert.deepStrictEqual(requests, ['POST /api/v1/imports/login-flows', 'DELETE /api/v1/imports/login-flows/f1']);
  // A poll already scheduled finds no flow and asks nothing.
  await timers.shift()();
  assert.equal(requests.length, 2);
});
