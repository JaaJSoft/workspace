'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

const USERS = [
  { id: '1', username: 'alice', first_name: 'Alice', last_name: 'Doe' },
  { id: '2', username: 'bob', first_name: '', last_name: 'Martin' },
  { id: '3', username: 'carol', first_name: 'Carol', last_name: '' },
];

function makeLocal() {
  const ctx = loadScript('workspace/users/ui/static/users/ui/js/user_selector.js');
  return ctx.userSelector('evt', () => USERS);
}

test('local mode: empty query lists every user on open', () => {
  const sel = makeLocal();
  sel.openLocal();
  assert.equal(Array.from(sel.results).length, 3);
  assert.equal(sel.showDropdown, true);
});

test('local mode: filters by username substring', async () => {
  const sel = makeLocal();
  sel.query = 'aro';
  await sel.search();
  assert.deepEqual(Array.from(sel.results).map((u) => u.username), ['carol']);
});

test('local mode: filters by first/last name, case-insensitive', async () => {
  const sel = makeLocal();
  sel.query = 'MARTIN';
  await sel.search();
  assert.deepEqual(Array.from(sel.results).map((u) => u.username), ['bob']);
});

test('local mode: single character queries filter (no 2-char minimum)', async () => {
  const sel = makeLocal();
  sel.query = 'b';
  await sel.search();
  assert.deepEqual(Array.from(sel.results).map((u) => u.username), ['bob']);
});

test('remote mode: short query clears results, never fetches, focus is inert', async () => {
  let fetched = false;
  const ctx = loadScript('workspace/users/ui/static/users/ui/js/user_selector.js', {
    fetch: () => {
      fetched = true;
      return Promise.resolve({ ok: false });
    },
  });
  const sel = ctx.userSelector('evt');
  sel.query = 'a';
  await sel.search();
  assert.equal(fetched, false);
  assert.equal(Array.from(sel.results).length, 0);
  sel.openLocal();
  assert.equal(sel.showDropdown, false);
});
