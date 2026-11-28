'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { loadScript } = require('./loader');

const LIST_URL = '/api/v1/things/t-uuid/comments';
const ITEM_URL = '/api/v1/things/comments';
const MENTIONS_URL = '/api/v1/things/t-uuid/users';
const FIRST_PAGE = `${LIST_URL}?limit=200&offset=0`;

// A list answer: a bare array, with the header that says whether more follows.
function page(rows, hasMore = false) {
  return {
    ok: true,
    headers: { get: (name) => (name === 'X-Has-More' ? String(hasMore) : null) },
    json: async () => rows,
  };
}

function make({ canComment = true, fetchImpl, mentions = MENTIONS_URL } = {}) {
  const calls = [];
  const defaultFetch = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    return page([]);
  };
  const ctx = loadScript('workspace/common/static/ui/js/comments.js', {
    fetch: fetchImpl || defaultFetch,
    getCSRFToken: () => 'tok',
  });
  const comp = ctx.commentsComponent(
    { list: LIST_URL, item: ITEM_URL, mentions },
    7,
    canComment,
  );
  comp.$refs = {};
  return { comp, calls };
}

test('a comment is addressed under the item URL, the thread under the list', () => {
  const { comp } = make();
  assert.equal(comp._url(), LIST_URL);
  assert.equal(comp._url('c1'), `${ITEM_URL}/c1`);
});

test('init asks who can be mentioned once, then loads the thread', async () => {
  const { comp, calls } = make();
  await comp.init();
  await comp.loadComments();
  assert.deepEqual(
    calls.map((c) => c.url),
    [MENTIONS_URL, FIRST_PAGE, FIRST_PAGE],
  );
});

test('a thread with nobody to mention asks for no list of them', async () => {
  const { comp, calls } = make({ mentions: '' });
  await comp.init();
  assert.deepEqual(calls.map((c) => c.url), [FIRST_PAGE]);
});

test('loadComments reads every page of a paged thread', async () => {
  const pages = [page([{ uuid: 'c1' }, { uuid: 'c2' }], true), page([{ uuid: 'c3' }])];
  const urls = [];
  const { comp } = make({
    fetchImpl: async (url) => {
      urls.push(url);
      return pages.shift();
    },
  });
  await comp.loadComments();
  assert.deepEqual(urls, [FIRST_PAGE, `${LIST_URL}?limit=200&offset=200`]);
  assert.deepEqual(Array.from(comp.comments, (c) => c.uuid), ['c1', 'c2', 'c3']);
});

test('a page that fails keeps the thread already shown', async () => {
  const pages = [page([{ uuid: 'new' }], true), { ok: false }];
  const { comp } = make({ fetchImpl: async () => pages.shift() });
  comp.comments = [{ uuid: 'shown' }];
  await comp.loadComments();
  assert.deepEqual(Array.from(comp.comments, (c) => c.uuid), ['shown']);
  assert.equal(comp.loading, false);
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
      ['PATCH', `${ITEM_URL}/c1`],
      ['GET', FIRST_PAGE],
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
    fetchImpl: async (url) =>
      page(url === MENTIONS_URL ? USERS : [{ uuid: 'c1', body: 'hi', body_html: 'hi' }]),
  });
  // The tests below read the thread themselves; the mentions come with init.
  const loadComments = made.comp.loadComments.bind(made.comp);
  made.comp.loadComments = async () => {
    await made.comp.loadMentionUsers();
    await loadComments();
  };
  return made;
}

test('the thread and the users to mention are read apart', async () => {
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

test('hasDraft holds a comment being written or edited', () => {
  const { comp } = make();
  assert.equal(comp.hasDraft(), false);
  comp.newBody = '   ';
  assert.equal(comp.hasDraft(), false);
  comp.newBody = 'A comment in progress';
  assert.equal(comp.hasDraft(), true);
  comp.newBody = '';
  comp.startEdit({ uuid: 'c1', body: 'old' });
  assert.equal(comp.hasDraft(), true);
  comp.cancelEdit();
  assert.equal(comp.hasDraft(), false);
});
