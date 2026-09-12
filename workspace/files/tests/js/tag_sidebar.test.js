'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScripts } = require('../../../common/tests/js/loader');

function makeSidebar(extra = {}) {
  const ctx = loadScripts(
    ['workspace/files/ui/static/files/ui/js/tags.js', 'workspace/files/ui/static/files/ui/js/tag_sidebar.js'],
    {
      getCSRFToken: () => 'token',
      document: { getElementById: () => null },
      CustomEvent: class { constructor(type) { this.type = type; } },
      ...extra,
    },
  );
  return ctx.tagSidebar();
}

test('the dialogs are requested from the browser component through window events', () => {
  const events = [];
  const sidebar = makeSidebar({ dispatchEvent: (e) => events.push(e.type) });
  sidebar.openTagManager();
  sidebar.showTagModal();
  assert.deepStrictEqual(events, ['open-tag-manager', 'open-tag-dialog']);
});

test('the active row follows the sidebar active view', () => {
  const sidebar = makeSidebar();
  sidebar.activeView = 'tag:abc';
  assert.equal(sidebar.isTagViewActive({ uuid: 'abc' }), true);
  assert.equal(sidebar.isTagViewActive({ uuid: 'def' }), false);
  assert.equal(sidebar.tagViewHref({ uuid: 'abc' }), '/files?tag=abc');
});

test('init loads the tags and reloads them on every tags-changed', async () => {
  let fetches = 0;
  const listeners = {};
  const sidebar = makeSidebar({
    fetch: async () => { fetches += 1; return { ok: true, json: async () => [] }; },
    addEventListener: (type, fn) => { listeners[type] = fn; },
  });
  sidebar.init();
  await listeners['tags-changed']();
  assert.equal(fetches, 2);
});
