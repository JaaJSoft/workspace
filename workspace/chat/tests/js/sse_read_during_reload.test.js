'use strict';

// A read receipt that arrives while the message list is reloading was not
// seen by that reload's response. The refresh it calls for must be a request
// of its own: alpine-ajax hands a GET for a URL already in flight the answer
// of that first request, written before the read.

const test = require('node:test');
const assert = require('node:assert');
const { loadScripts } = require('../../../common/tests/js/loader');

function node(id, dataset = {}) {
  return { id, dataset, children: [], replaceChildren() { this.children = []; } };
}

function buildApp() {
  const nodes = {
    'message-list': node('message-list'),
    'message-list-state': node('message-list-state', { offset: '0', end: '20', hasNewer: 'false' }),
    'message-list-items': node('message-list-items'),
  };
  const page = { pendingReceipt: false };
  const timers = [];
  const ctx = loadScripts(
    ['workspace/chat/ui/static/chat/ui/js/messages.js', 'workspace/chat/ui/static/chat/ui/js/sse.js'],
    {
      setTimeout: (fn) => { timers.push(fn); return timers.length; },
      clearTimeout: () => {},
      document: {
        getElementById: (id) => nodes[id] || null,
        querySelector: (selector) => (selector === '[data-read-pending]' && page.pendingReceipt ? {} : null),
      },
    },
  );

  // A request-reuse model of $ajax: a GET for a URL still in flight shares it.
  const requests = [];
  const inFlight = new Map();
  const $ajax = (url, options) => {
    if (inFlight.has(url)) return inFlight.get(url).promise;
    let answer;
    const promise = new Promise((resolve) => { answer = resolve; });
    const request = {
      url,
      promise,
      land() {
        inFlight.delete(url);
        answer([nodes[options.targets[0]]]);
      },
    };
    requests.push(request);
    inFlight.set(url, request);
    return promise;
  };

  const app = { ...ctx.chatMessagesMixin(), ...ctx.chatSseMixin() };
  Object.assign(app, {
    activeConversation: { uuid: 'c1' },
    $refs: { messagesContainer: { scrollTop: 0, scrollHeight: 100, clientHeight: 0 } },
    $nextTick(fn) { if (fn) fn(); },
    $ajax,
    _isNearBottom: () => false,
    scrollToBottom() {},
  });
  const runTimers = async () => {
    while (timers.length) await timers.shift()();
  };
  return { app, page, requests, runTimers };
}

test('a read during a reload is refreshed by a request sent after that reload landed', async () => {
  const { app, page, requests, runTimers } = buildApp();

  const reload = app._refreshCurrentMessages();
  const reading = app.handleSSERead({ conversation_id: 'c1' });
  // Past the refresh delay while the first request is still out.
  await runTimers();
  assert.equal(requests.length, 1);

  // The reload lands with one of the user's messages still waiting on readers.
  page.pendingReceipt = true;
  requests[0].land();
  await reload;
  await reading;
  await runTimers();

  assert.equal(requests.length, 2);
  assert.notStrictEqual(requests[1], requests[0]);
});

test('a read during a full load (jump to the present) is refreshed once that load landed', async () => {
  const { app, page, requests, runTimers } = buildApp();

  const loading = app.jumpToPresent();
  const reading = app.handleSSERead({ conversation_id: 'c1' });
  await runTimers();
  assert.equal(requests.length, 1);

  page.pendingReceipt = true;
  requests[0].land();
  await loading;
  await reading;
  await runTimers();

  assert.equal(requests.length, 2);
  assert.notStrictEqual(requests[1], requests[0]);
});
