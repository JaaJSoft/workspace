'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('./loader');

const LIST_URL = '/api/v1/projects/p-uuid/tasks/t-uuid/comments';

function make({ canComment = true, fetchImpl } = {}) {
  const calls = [];
  const defaultFetch = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    return { ok: true, json: async () => ({ comments: [], mention_users: [] }) };
  };
  const ctx = loadScript('workspace/common/static/ui/js/comments.js', {
    fetch: fetchImpl || defaultFetch,
    getCSRFToken: () => 'tok',
  });
  const comp = ctx.commentsComponent(LIST_URL, 7, canComment);
  comp.$refs = {};
  return { comp, calls };
}

test('builds list and detail URLs from the base', () => {
  const { comp } = make();
  assert.equal(comp._url(), LIST_URL);
  assert.equal(comp._url('c1'), `${LIST_URL}/c1`);
});

test('addComment posts the trimmed body then reloads the list', async () => {
  const { comp, calls } = make();
  comp.newBody = '  hello  ';
  await comp.addComment();
  assert.deepEqual(
    calls.map((c) => c.method),
    ['POST', 'GET'],
  );
  assert.equal(comp.newBody, '');
  assert.equal(comp.sending, false);
});

test('addComment is inert without canComment', async () => {
  const { comp, calls } = make({ canComment: false });
  comp.newBody = 'hello';
  await comp.addComment();
  assert.equal(calls.length, 0);
  assert.equal(comp.newBody, 'hello');
});

test('addComment is inert on empty body', async () => {
  const { comp, calls } = make();
  comp.newBody = '   ';
  await comp.addComment();
  assert.equal(calls.length, 0);
});

test('deleteComment and saveEdit are inert without canComment', async () => {
  const { comp, calls } = make({ canComment: false });
  comp.editBody = 'x';
  await comp.saveEdit('c1');
  await comp.deleteComment('c1');
  assert.equal(calls.length, 0);
});

test('startEdit/cancelEdit toggle the editing state', () => {
  const { comp } = make();
  comp.startEdit({ uuid: 'c1', body: 'original' });
  assert.equal(comp.editingId, 'c1');
  assert.equal(comp.editBody, 'original');
  comp.cancelEdit();
  assert.equal(comp.editingId, null);
  assert.equal(comp.editBody, '');
});

test('saveEdit patches then clears the editing state', async () => {
  const { comp, calls } = make();
  comp.startEdit({ uuid: 'c1', body: 'original' });
  comp.editBody = 'fixed';
  await comp.saveEdit('c1');
  assert.deepEqual(
    calls.map((c) => [c.method, c.url]),
    [
      ['PATCH', `${LIST_URL}/c1`],
      ['GET', LIST_URL],
    ],
  );
  assert.equal(comp.editingId, null);
});

test('failed load leaves comments empty and stops the spinner', async () => {
  const { comp } = make({
    fetchImpl: async () => {
      throw new Error('network down');
    },
  });
  await comp.loadComments();
  assert.deepEqual(Array.from(comp.comments), []);
  assert.equal(comp.loading, false);
});

test('autoGrow sizes the element to its content plus borders', () => {
  const { comp } = make();
  const el = { style: {}, scrollHeight: 100, offsetHeight: 52, clientHeight: 50 };
  comp.autoGrow(el);
  assert.equal(el.style.height, '102px');
});

// ── Mentions ─────────────────────────────────────────────────

const USERS = [
  { id: 7, username: 'me', first_name: 'Me', last_name: '' },
  { id: 1, username: 'alice', first_name: 'Alice', last_name: 'Wonder' },
  { id: 2, username: 'bob', first_name: 'Bob', last_name: 'Builder' },
];

function makeWithUsers() {
  const made = make({
    fetchImpl: async () => ({
      ok: true,
      json: async () => ({
        comments: [{ uuid: 'c1', body: 'hi', body_html: 'hi' }],
        mention_users: USERS,
      }),
    }),
  });
  return made;
}

test('loadComments parses comments and mention users', async () => {
  const { comp } = makeWithUsers();
  await comp.loadComments();
  assert.equal(comp.comments.length, 1);
  assert.equal(comp.comments[0].body_html, 'hi');
  assert.equal(comp.mentionUsers.length, 3);
});

test('filterMentionResults excludes self and matches query', async () => {
  const { comp } = makeWithUsers();
  await comp.loadComments();
  comp.mentionQuery = '';
  comp.filterMentionResults();
  assert.deepEqual(
    Array.from(comp.mentionResults, (u) => u.username),
    ['alice', 'bob'],
  );
  comp.mentionQuery = 'wond';
  comp.filterMentionResults();
  assert.deepEqual(
    Array.from(comp.mentionResults, (u) => u.username),
    ['alice'],
  );
  assert.equal(comp.mentionHighlight, 0);
});

test('handleMentionInput activates on @ after whitespace only', async () => {
  const { comp } = makeWithUsers();
  await comp.loadComments();
  const el = { value: 'hello @al', selectionStart: 9 };
  comp.handleMentionInput(el, 'new');
  assert.equal(comp.mentionActive, true);
  assert.equal(comp.mentionQuery, 'al');
  assert.equal(comp.mentionStartPos, 6);

  const midWord = { value: 'mail foo@al', selectionStart: 11 };
  comp.handleMentionInput(midWord, 'new');
  assert.equal(comp.mentionActive, false);
});

test('insertMention splices the username into the composer body', async () => {
  const { comp } = makeWithUsers();
  await comp.loadComments();
  const el = { value: 'hello @al there', selectionStart: 9 };
  comp.newBody = el.value;
  comp.handleMentionInput(el, 'new');
  comp.insertMention({ username: 'alice' });
  assert.equal(comp.newBody, 'hello @alice  there');
  assert.equal(comp.mentionActive, false);
  assert.equal(comp.mentionResults.length, 0);
});

test('insertMention targets the edit body when editing', async () => {
  const { comp } = makeWithUsers();
  await comp.loadComments();
  const el = { value: 'fix @b', selectionStart: 6 };
  comp.editBody = el.value;
  comp.handleMentionInput(el, 'edit');
  comp.insertMention({ username: 'bob' });
  assert.equal(comp.editBody, 'fix @bob ');
  assert.equal(comp.newBody, '');
});

// Django usernames allow [.@+-]; the dropdown must keep filtering across them.

test('handleMentionInput keeps filtering past a dot', async () => {
  const { comp } = makeWithUsers();
  await comp.loadComments();
  const el = { value: 'hello @jean.du', selectionStart: 14 };
  comp.handleMentionInput(el, 'new');
  assert.equal(comp.mentionActive, true);
  assert.equal(comp.mentionQuery, 'jean.du');
  assert.equal(comp.mentionStartPos, 6);
});

test('handleMentionInput keeps filtering past a hyphen', async () => {
  const { comp } = makeWithUsers();
  await comp.loadComments();
  const el = { value: 'hi @marie-cl', selectionStart: 12 };
  comp.handleMentionInput(el, 'new');
  assert.equal(comp.mentionActive, true);
  assert.equal(comp.mentionQuery, 'marie-cl');
});

test('an email typed after whitespace never opens the dropdown', async () => {
  const { comp } = makeWithUsers();
  await comp.loadComments();
  const el = { value: 'write to alice@exa', selectionStart: 18 };
  comp.handleMentionInput(el, 'new');
  assert.equal(comp.mentionActive, false);
});

test('insertMention splices a dotted username', async () => {
  const { comp } = makeWithUsers();
  await comp.loadComments();
  const el = { value: 'hello @jean.du there', selectionStart: 14 };
  comp.newBody = el.value;
  comp.handleMentionInput(el, 'new');
  comp.insertMention({ username: 'jean.dupont' });
  assert.equal(comp.newBody, 'hello @jean.dupont  there');
});

test('handleMentionKeydown cycles and inserts with Enter', async () => {
  const { comp } = makeWithUsers();
  await comp.loadComments();
  const el = { value: '@', selectionStart: 1 };
  comp.newBody = el.value;
  comp.handleMentionInput(el, 'new');
  assert.equal(comp.mentionResults.length, 2);

  let prevented = 0;
  const key = (k, extra = {}) => ({ key: k, preventDefault: () => { prevented += 1; }, ...extra });
  comp.handleMentionKeydown(key('ArrowDown'));
  assert.equal(comp.mentionHighlight, 1);
  comp.handleMentionKeydown(key('ArrowUp'));
  assert.equal(comp.mentionHighlight, 0);
  comp.handleMentionKeydown(key('Enter'));
  assert.equal(comp.newBody, '@alice ');
  assert.equal(comp.mentionActive, false);
  assert.equal(prevented, 3);
});

test('handleMentionKeydown ignores ctrl+enter and inactive state', async () => {
  const { comp } = makeWithUsers();
  await comp.loadComments();
  let prevented = 0;
  const ev = { key: 'Enter', ctrlKey: true, preventDefault: () => { prevented += 1; } };
  comp.handleMentionKeydown(ev);
  assert.equal(prevented, 0);

  const el = { value: '@', selectionStart: 1 };
  comp.newBody = el.value;
  comp.handleMentionInput(el, 'new');
  comp.handleMentionKeydown(ev);
  assert.equal(prevented, 0);
  assert.equal(comp.newBody, '@');
});
