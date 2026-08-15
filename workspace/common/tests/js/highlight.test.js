'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScripts } = require('../../../common/tests/js/loader');

// highlight.js calls escapeHtml, which html.js defines; base.html loads both.
const ctx = loadScripts([
  'workspace/common/static/ui/js/html.js',
  'workspace/common/static/ui/js/highlight.js',
]);
const { highlightMatch } = ctx;

const MARK = 'bg-warning/40 text-inherit rounded-sm px-0.5';
const mark = (s) => `<mark class="${MARK}">${s}</mark>`;

test('wraps the matched term and leaves the rest alone', () => {
  assert.equal(highlightMatch('Meeting notes', 'notes'), `Meeting ${mark('notes')}`);
});

test('matches case-insensitively but keeps the text casing', () => {
  assert.equal(highlightMatch('Meeting Notes', 'notes'), `Meeting ${mark('Notes')}`);
});

test('marks every occurrence, not just the first', () => {
  assert.equal(
    highlightMatch('note note', 'note'),
    `${mark('note')} ${mark('note')}`
  );
});

test('returns the escaped text untouched when nothing matches', () => {
  assert.equal(highlightMatch('Meeting notes', 'zzz'), 'Meeting notes');
});

test('escapes the text before marking it (x-html sink)', () => {
  assert.equal(
    highlightMatch('<img src=x onerror=alert(1)> report', 'report'),
    `&lt;img src=x onerror=alert(1)&gt; ${mark('report')}`
  );
});

test('treats regex metacharacters in the query as literals', () => {
  assert.equal(
    highlightMatch('price (12.50) [sale]', '(12.50)'),
    `price ${mark('(12.50)')} [sale]`
  );
  // A bare quantifier would make the regex constructor throw.
  assert.equal(highlightMatch('a*b', '*'), `a${mark('*')}b`);
  assert.equal(highlightMatch('a.b acb', '.'), `a${mark('.')}b acb`);
});

test('returns the escaped text when the query is empty', () => {
  assert.equal(highlightMatch('Tom & Jerry', ''), 'Tom &amp; Jerry');
  assert.equal(highlightMatch('Tom & Jerry', null), 'Tom &amp; Jerry');
});

test('coerces empty and nullish text to an empty string', () => {
  assert.equal(highlightMatch('', 'a'), '');
  assert.equal(highlightMatch(null, 'a'), '');
  assert.equal(highlightMatch(undefined, 'a'), '');
});

test('escape: false leaves pre-rendered HTML markup intact', () => {
  assert.equal(
    highlightMatch('<p>Meeting notes</p>', 'notes', { escape: false }),
    `<p>Meeting ${mark('notes')}</p>`
  );
});

test('escape: false hands back a falsy body unchanged', () => {
  // The chat panel binds the result straight to x-html, which renders nothing
  // for undefined - same outcome as the empty string, without coercing it.
  assert.equal(highlightMatch(undefined, 'a', { escape: false }), undefined);
  assert.equal(highlightMatch('<p>hi</p>', '', { escape: false }), '<p>hi</p>');
});

// Regression: the query used to be regex-escaped but not HTML-escaped, so it
// was matched against text that had been. A term holding an HTML
// metacharacter therefore either missed entirely or landed inside an entity
// and split it, emitting `<mark>&</mark>amp;` - which renders as the literal
// text "&amp;".
test('matches a query whose HTML metacharacters are entities in the text', () => {
  assert.equal(highlightMatch('Say "hi"', '"hi"'), `Say ${mark('&quot;hi&quot;')}`);
  assert.equal(highlightMatch('a < b', '<'), `a ${mark('&lt;')} b`);
});

test('marks an ampersand as a whole entity', () => {
  assert.equal(highlightMatch('Tom & Jerry', '&'), `Tom ${mark('&amp;')} Jerry`);
});

test('escapes the query for HTML before neutralizing its regex metacharacters', () => {
  assert.equal(
    highlightMatch('total (a & b) due', '(a & b)'),
    `total ${mark('(a &amp; b)')} due`
  );
});

test('escapes the apostrophe alongside the text it is matched against', () => {
  assert.equal(highlightMatch("don't stop", "don't"), `${mark('don&#39;t')} stop`);
});

// The markdown renderer that produces chat bodies escapes only `&<>"`, so an
// apostrophe reaches the browser literally. Escaping the query's would make
// every term holding one unmatchable on that path.
test('escape: false leaves the apostrophe raw to match a pre-escaped body', () => {
  assert.equal(
    highlightMatch("<p>don't stop</p>", "don't", { escape: false }),
    `<p>${mark("don't")} stop</p>`
  );
});

test('escape: false matches the entities a pre-escaped body carries', () => {
  assert.equal(
    highlightMatch('<p>Tom &amp; Jerry</p>', '&', { escape: false }),
    `<p>Tom ${mark('&amp;')} Jerry</p>`
  );
  assert.equal(
    highlightMatch('<p>say &quot;hi&quot;</p>', '"hi"', { escape: false }),
    `<p>say ${mark('&quot;hi&quot;')}</p>`
  );
});

test('escape: false keeps a "<" query out of the surrounding markup', () => {
  // Unescaped, this matched the `<` of `<p>` and cut the tag in half.
  assert.equal(
    highlightMatch('<p>a &lt; b</p>', '<', { escape: false }),
    `<p>a ${mark('&lt;')} b</p>`
  );
});
