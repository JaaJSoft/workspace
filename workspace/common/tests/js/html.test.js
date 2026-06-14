'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('./loader');

const ctx = loadScript('workspace/common/static/ui/js/html.js');
const { escapeHtml } = ctx;

test('neutralizes HTML tags (XSS guard)', () => {
  assert.equal(
    escapeHtml('<img src=x onerror=alert(1)>'),
    '&lt;img src=x onerror=alert(1)&gt;'
  );
});

test('escapes the ampersand first so entities are not double-escaped', () => {
  assert.equal(escapeHtml('a & b'), 'a &amp; b');
  assert.equal(escapeHtml('<'), '&lt;'); // not &amp;lt;
});

test('escapes both quote characters for attribute-context safety', () => {
  assert.equal(escapeHtml('a & "b"'), 'a &amp; &quot;b&quot;');
  assert.equal(escapeHtml("it's a <test>"), 'it&#39;s a &lt;test&gt;');
});

test('coerces nullish input to an empty string', () => {
  assert.equal(escapeHtml(null), '');
  assert.equal(escapeHtml(undefined), '');
});

test('coerces non-string input via String()', () => {
  assert.equal(escapeHtml(42), '42');
});
