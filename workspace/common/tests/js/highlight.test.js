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

// Characterization, not endorsement: the query is regex-escaped but not
// HTML-escaped, so it is matched against text that has been. A query holding an
// HTML metacharacter therefore misses (`"hi"` never matches `&quot;hi&quot;`)
// or lands inside an entity and splits it. Behaviour predates this helper and
// is unchanged by it; these two assertions are here so that fixing it has to be
// a deliberate edit rather than a silent one.
test('an HTML metacharacter in the query does not match its escaped text', () => {
  assert.equal(highlightMatch('Say "hi"', '"hi"'), 'Say &quot;hi&quot;');
});

test('an ampersand query splits the entity it lands in', () => {
  assert.equal(highlightMatch('Tom & Jerry', '&'), `Tom ${mark('&')}amp; Jerry`);
});
