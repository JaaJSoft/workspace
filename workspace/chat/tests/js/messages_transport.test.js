'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

/**
 * Where every message request goes, and with which headers.
 *
 * The guest meeting page spreads this same mixin and points it at the
 * token-authenticated /meet endpoints, so the addressing is now a seam
 * rather than a literal. These tests pin the member side of that seam: the
 * conversation being viewed, the conversation being marked read and the
 * conversation the HTML list is fetched for are three DISTINCT ids, so a
 * site that reads the wrong source still fails even though it produced a
 * perfectly well-formed URL.
 */
const VIEWED = 'conv-viewed';
const READ = 'conv-read';

function buildApp(overrides = {}) {
  const calls = [];
  const ajaxCalls = [];
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/messages.js', {
    getCSRFToken: () => 'csrf-token',
    FormData: class {
      constructor() { this.entries = []; }
      append(key, value) { this.entries.push([key, value]); }
    },
    document: { getElementById: () => null },
    AppAlert: { error() {} },
    AppDialog: { confirm: async () => true },
    fetch: async (url, opts = {}) => {
      calls.push({
        url,
        method: opts.method || 'GET',
        headers: opts.headers || {},
        body: opts.body,
        credentials: opts.credentials,
      });
      return { ok: true, status: 200, json: async () => ({ uuid: 'm1', body: 'hi', body_html: 'hi' }) };
    },
  });

  const app = ctx.chatMessagesMixin();
  Object.assign(app, {
    activeConversation: { uuid: VIEWED, kind: 'group', members: [] },
    messageBody: 'hello',
    pendingFiles: [],
    pendingPickedFiles: [],
    replyingTo: null,
    botTyping: false,
    currentUserId: 7,
    $nextTick(fn) { if (fn) fn(); },
    async $ajax(url, options) {
      ajaxCalls.push({ url, options });
      return [];
    },
    _clearDraft() {},
    _updateConversationLastMessage() {},
    refreshConversationItems() {},
    isBotConversation() { return false; },
    scrollToBottom() {},
    _injectOptimisticMessage() {},
    _removeOptimisticMessage() {},
    _messageEls() { return []; },
    ...overrides,
  });
  return { app, calls, ajaxCalls };
}

test('a plain send posts JSON to the viewed conversation, with the CSRF token', async () => {
  const { app, calls } = buildApp();
  await app.sendMessage();

  const post = calls.find((c) => c.method === 'POST');
  assert.equal(post.url, `/api/v1/chat/conversations/${VIEWED}/messages`);
  assert.equal(post.headers['Content-Type'], 'application/json');
  assert.equal(post.headers['X-CSRFToken'], 'csrf-token');
  assert.equal(post.credentials, 'same-origin');
  assert.deepStrictEqual(JSON.parse(post.body), { body: 'hello' });
});

test('a send carrying files posts multipart, so it must not claim a JSON content type', async () => {
  const { app, calls } = buildApp({ pendingFiles: [{ name: 'a.png' }] });
  await app.sendMessage();

  const post = calls.find((c) => c.method === 'POST');
  assert.equal(post.url, `/api/v1/chat/conversations/${VIEWED}/messages`);
  assert.equal(post.headers['Content-Type'], undefined);
  assert.equal(post.headers['X-CSRFToken'], 'csrf-token');
});

test('a voice message goes to the same collection endpoint', async () => {
  const { app, calls } = buildApp();
  await app.sendVoiceMessage({ name: 'voice.webm' }, 4);

  const post = calls.find((c) => c.method === 'POST');
  assert.equal(post.url, `/api/v1/chat/conversations/${VIEWED}/messages`);
  assert.equal(post.headers['X-CSRFToken'], 'csrf-token');
});

test('an edit patches the message under the viewed conversation', async () => {
  const { app, calls } = buildApp();
  app.editingMessageUuid = 'm42';
  app.messageBody = 'fixed';
  await app.saveEdit();

  const patch = calls.find((c) => c.method === 'PATCH');
  assert.equal(patch.url, `/api/v1/chat/conversations/${VIEWED}/messages/m42`);
  assert.equal(patch.headers['Content-Type'], 'application/json');
  assert.equal(patch.headers['X-CSRFToken'], 'csrf-token');
});

test('a delete addresses the message under the viewed conversation', async () => {
  const { app, calls } = buildApp();
  await app.deleteMessage('m42');

  const del = calls.find((c) => c.method === 'DELETE');
  assert.equal(del.url, `/api/v1/chat/conversations/${VIEWED}/messages/m42`);
  assert.equal(del.headers['X-CSRFToken'], 'csrf-token');
});

test('mark-as-read addresses the conversation it is handed, not the viewed one', async () => {
  const { app, calls } = buildApp();
  await app.markAsRead(READ);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, `/api/v1/chat/conversations/${READ}/read`);
  assert.equal(calls[0].method, 'POST');
});

test('the HTML list is fetched for the viewed conversation, cursor included', async () => {
  const { app, ajaxCalls } = buildApp();
  assert.equal(app._messagesUrl(null), `/chat/${VIEWED}/messages`);
  assert.equal(app._messagesUrl('m9'), `/chat/${VIEWED}/messages?before=m9`);

  await app._refreshCurrentMessages();
  assert.equal(ajaxCalls[0].url, `/chat/${VIEWED}/messages`);
});
