'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const ctx = loadScript('workspace/calendar/ui/static/calendar/ui/js/invitee_selector.js');

const bob = { id: 2, username: 'bob', first_name: 'Bob', last_name: '' };
const linkedBob = {
  uuid: 'p-bob', display_name: 'Bob Builder', emails: [], linked_user: { id: 2, username: 'bob' },
};
const linkedCarl = {
  uuid: 'p-carl', display_name: 'Carl', emails: [], linked_user: { id: 3, username: 'carl' },
};
const ada = {
  uuid: 'p-ada',
  display_name: 'Ada Lovelace',
  emails: [{ value: 'ada@example.com', type: 'work' }, { value: 'ada@home.example', type: 'home' }],
  linked_user: null,
};
const phoneOnly = { uuid: 'p-dan', display_name: 'Dan', emails: [], linked_user: null };

function summarize(results) {
  return Array.from(results, (r) => `${r.kind}:${r.key.split(':')[1]}${r.disabled ? ':disabled' : ''}`);
}

test('groups accounts, contacts and lists in that order', () => {
  const results = ctx.inviteeResults(
    'fam', [bob], [ada], [{ uuid: 'l-1', name: 'Family', member_count: 3 }],
  );
  assert.deepStrictEqual(summarize(results), ['user:2', 'person:p-ada', 'list:l-1']);
});

test('drops a contact linked to an account already listed', () => {
  const results = ctx.inviteeResults('bo', [bob], [linkedBob, linkedCarl], []);
  assert.deepStrictEqual(summarize(results), ['user:2', 'person:p-carl']);
  assert.strictEqual(results[1].linked, true);
});

test('an external contact carries its first email', () => {
  const [row] = ctx.inviteeResults('ada', [], [ada], []);
  assert.strictEqual(row.linked, false);
  assert.strictEqual(row.email, 'ada@example.com');
  assert.strictEqual(row.disabled, false);
});

test('a contact with neither account nor email is shown disabled', () => {
  const [row] = ctx.inviteeResults('dan', [], [phoneOnly], []);
  assert.strictEqual(row.disabled, true);
});

test('lists are filtered by name and an empty one is disabled', () => {
  const lists = [
    { uuid: 'l-1', name: 'Family', member_count: 3 },
    { uuid: 'l-2', name: 'Work', member_count: 5 },
    { uuid: 'l-3', name: 'Far cousins', member_count: 0 },
  ];
  const results = ctx.inviteeResults('FA', [], [], lists);
  assert.deepStrictEqual(summarize(results), ['list:l-1', 'list:l-3:disabled']);
});

test('keyboard navigation skips disabled rows', () => {
  const selector = ctx.inviteeSelector('evt');
  selector.results = ctx.inviteeResults('a', [], [phoneOnly, ada], []);
  selector.highlight = -1;
  selector.moveHighlight(1);
  assert.strictEqual(selector.highlight, 1);
  selector.moveHighlight(1);
  assert.strictEqual(selector.highlight, 1);
  selector.moveHighlight(-1);
  assert.strictEqual(selector.highlight, 1);
});
