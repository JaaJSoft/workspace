// The two things this helper exists to remember, both learned the hard way in
// the emergency kit: the anchor has to be in the document, and the object URL
// has to be revoked - but not before the browser has read it.
const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

const SCRIPT = 'workspace/common/static/ui/js/download.js';

function load() {
  const events = [];
  let deferred = null;
  const anchor = {
    click: () => events.push('click'),
    remove: () => events.push('remove'),
    set href(v) { events.push(`href:${v}`); },
    set download(v) { events.push(`download:${v}`); },
  };
  const ctx = loadScript(SCRIPT, {
    document: {
      createElement: () => anchor,
      body: { appendChild: () => events.push('append') },
    },
    URL: {
      createObjectURL: () => 'blob:x',
      revokeObjectURL: () => events.push('revoke'),
    },
    // Captured, not run: a stub that invokes the callback immediately cannot
    // tell a deferred revocation from a synchronous one.
    setTimeout: (fn) => { deferred = fn; },
  });
  return { ctx, events, runDeferred: () => deferred && deferred() };
}

test('the anchor is inserted before it is clicked', () => {
  // Firefox ignores a click on an anchor that was never inserted, and the
  // user is left with no file and no error.
  const { ctx, events } = load();
  ctx.downloadBlob({}, 'f.txt');
  assert.ok(events.indexOf('append') < events.indexOf('click'), events.join(','));
});

test('the object url is not revoked in the same tick as the click', () => {
  // Revoking synchronously races the read the click has just started, and the
  // user gets an empty file with nothing to explain it.
  const { ctx, events, runDeferred } = load();
  ctx.downloadBlob({}, 'f.txt');
  assert.ok(!events.includes('revoke'), 'revoked before the browser could read the blob');
  runDeferred();
  assert.ok(events.includes('revoke'), 'never revoked at all');
});

test('the filename reaches the anchor', () => {
  const { ctx, events } = load();
  ctx.downloadBlob({}, 'vault-export-2026-09-06.vaultarchive');
  assert.ok(events.includes('download:vault-export-2026-09-06.vaultarchive'));
});
