const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

function app(fetchStub) {
  const ctx = loadScript('workspace/imports/ui/static/imports/ui/js/imports.js', {
    document: { getElementById: () => null, addEventListener: () => {} },
    getCSRFToken: () => 'x',
    formatFileSize: (b) => `${b} B`,
    fetch: fetchStub || (() => Promise.reject(new Error('no network in tests'))),
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
