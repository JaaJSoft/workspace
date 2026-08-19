'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

// Spy on the global alert entry point, normalizing every AppAlert method to
// a (type, message) pair so assertions don't depend on which one the
// component reaches for.
function alertSpy(calls) {
  return {
    show: (o) => calls.push([o.type, o.message]),
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
