const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

function app() {
  const ctx = loadScript('workspace/imports/ui/static/imports/ui/js/imports.js', {
    document: { getElementById: () => null, addEventListener: () => {} },
    getCSRFToken: () => 'x',
    formatFileSize: (b) => `${b} B`,
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
