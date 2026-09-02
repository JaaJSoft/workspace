'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

// Spy on the global alert entry point, normalizing every AppAlert method to
// a (type, message) pair so assertions don't depend on which one the
// component reaches for.
function alertSpy(calls) {
  return {
    show: (o) => { calls.push([o.type, o.message]); return { toast: o.message }; },
    dismiss: (el) => calls.push(['dismiss', el.toast]),
    info: (m) => calls.push(['info', m]),
    success: (m) => calls.push(['success', m]),
    warning: (m) => calls.push(['warning', m]),
    error: (m) => calls.push(['error', m]),
  };
}

function makeBrowser({ clipboard = {} } = {}) {
  const calls = [];
  const ctx = loadScript('workspace/files/ui/static/files/ui/js/browser.js', {
    tagsMixin: () => ({ toggleFileTag: async () => {} }),
    // cut/copy stamp each item with the current folder, read off the DOM.
    document: { getElementById: () => null },
  });
  ctx.AppAlert = alertSpy(calls);
  ctx.fileClipboard = { cut: () => {}, copy: () => {}, getItems: () => [], ...clipboard };
  return { browser: ctx.fileBrowser(), calls, ctx };
}

test('cutting to the clipboard announces the count through the global alert', () => {
  const clipped = [];
  const { browser, calls } = makeBrowser({ clipboard: { cut: (items) => clipped.push(items) } });
  browser.cutToClipboard([{ uuid: 'a' }, { uuid: 'b' }]);
  assert.equal(clipped.length, 1);
  assert.deepEqual(Array.from(calls), [['info', '2 items cut to clipboard']]);
});

test('copying a single item announces it in the singular', () => {
  const { browser, calls } = makeBrowser();
  browser.copyToClipboard([{ uuid: 'a' }]);
  assert.deepEqual(Array.from(calls), [['info', '1 item copied to clipboard']]);
});

test('pasting an empty clipboard warns instead of failing silently', async () => {
  const { browser, calls } = makeBrowser({ clipboard: { getItems: () => [] } });
  await browser.pasteFromClipboard();
  assert.deepEqual(Array.from(calls), [['warning', 'Clipboard is empty']]);
});

// The bulk-download endpoint answers a refusal with {detail: "..."}; the
// user has to see that reason, not a generic failure that hides it.
// Timers default to never firing: the "preparing" notice is for a slow
// archive, and each test decides whether the wait elapsed.
function makeBrowserWithDownload(response, { slow = false } = {}) {
  const { browser, calls, ctx } = makeBrowser();
  const timers = [];
  ctx.getCSRFToken = () => 'token';
  ctx.fetch = async () => response;
  ctx.setTimeout = (fn) => { if (slow) fn(); timers.push(fn); return timers.length; };
  ctx.clearTimeout = (id) => calls.push(['clearTimeout', id]);
  ctx.URL = { createObjectURL: () => 'blob:zip', revokeObjectURL: () => {} };
  ctx.document.createElement = () => ({ click() {}, remove() {} });
  ctx.document.body = { appendChild() {} };
  return { browser, calls };
}

test('a refused bulk download surfaces the server reason and drops the notice', async () => {
  const { browser, calls } = makeBrowserWithDownload({
    ok: false,
    json: async () => ({ detail: 'One or more UUIDs not found.' }),
  }, { slow: true });
  await browser.bulkDownload(['a', 'b']);
  assert.deepEqual(Array.from(calls), [
    ['info', 'Preparing an archive of 2 items...'],
    ['error', 'One or more UUIDs not found.'],
    ['clearTimeout', 1],
    ['dismiss', 'Preparing an archive of 2 items...'],
  ]);
});

test('a refused bulk download without a JSON body falls back to the generic message', async () => {
  const { browser, calls } = makeBrowserWithDownload({
    ok: false,
    json: async () => { throw new SyntaxError('not JSON'); },
  });
  await browser.bulkDownload(['a']);
  assert.deepEqual(Array.from(calls), [['error', 'Failed to download selected files'], ['clearTimeout', 1]]);
});

test('a slow bulk download announces the archive and drops the notice once it arrives', async () => {
  const { browser, calls } = makeBrowserWithDownload({
    ok: true,
    blob: async () => ({}),
  }, { slow: true });
  await browser.bulkDownload(['a']);
  assert.deepEqual(Array.from(calls), [
    ['info', 'Preparing an archive of 1 item...'],
    ['clearTimeout', 1],
    ['dismiss', 'Preparing an archive of 1 item...'],
  ]);
});

test('a fast bulk download never shows the notice', async () => {
  const { browser, calls } = makeBrowserWithDownload({
    ok: true,
    blob: async () => ({}),
  });
  await browser.bulkDownload(['a', 'b', 'c']);
  assert.deepEqual(Array.from(calls), [['clearTimeout', 1]]);
});
