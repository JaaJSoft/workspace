'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/mail/ui/static/mail/ui/js/mail.js');

const person = {
  kind: 'person', uuid: 'p1', name: 'Alice Martin', user_id: null, avatar_url: null,
  emails: [{ value: 'alice@work.com', type: 'work' }, { value: 'ally@home.org', type: 'home' }],
};
const account = { kind: 'account', user_id: 7, username: 'carol', name: 'Carol', email: 'carol@corp.com' };
const history = { kind: 'history', name: 'Bob', email: 'bob@example.com', count: 3 };

const rows = (suggestions, existing = []) =>
  Array.from(ctx._recipientRows(suggestions, new Set(existing))).map(r => ({ ...r }));

test('a person yields one row per address, the name on the first only', () => {
  const result = rows([person]);
  assert.deepEqual(result.map(r => [r.email, r.type, r.first]), [
    ['alice@work.com', 'work', true],
    ['ally@home.org', 'home', false],
  ]);
  assert.ok(result.every(r => r.uuid === 'p1' && r.name === 'Alice Martin'));
});

test('row keys are unique across kinds', () => {
  const keys = rows([person, account, history]).map(r => r.key);
  assert.equal(new Set(keys).size, keys.length);
});

test('sectionStart marks the first row of each kind', () => {
  assert.deepEqual(
    rows([person, account, history]).map(r => [r.kind, r.sectionStart]),
    [['person', true], ['person', false], ['account', true], ['history', true]],
  );
});

test('addresses already added are dropped, case-insensitively', () => {
  const result = rows([person, history], ['alice@work.com', 'bob@example.com']);
  assert.deepEqual(result.map(r => [r.email, r.first, r.sectionStart]), [
    ['ally@home.org', true, true],
  ]);
});

test('an existing address is matched against the lowercased suggestion', () => {
  const upper = { ...history, email: 'Bob@Example.com' };
  assert.deepEqual(rows([upper], ['bob@example.com']), []);
});
