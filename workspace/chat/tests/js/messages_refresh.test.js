'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

/**
 * Two things the refresh path owes the pane, both of them invisible until
 * the server is under load.
 *
 * Coalescing: the list is re-rendered whole, so a refresh issued while
 * another is in flight can only ever produce the answer the pending one
 * will produce. A thirty-message burst is one repaint, not thirty fetches.
 *
 * Failure: alpine-ajax treats a non-2xx as a response like any other - it
 * parses the body, finds no #message-list in it and would remove the live
 * one, which _onAjaxMissing then cancels. Nothing throws, so the catch in
 * _refreshCurrentMessages never runs and a rate-limited pane just stops
 * updating in silence.
 */

function node(id, dataset = {}) {
  return { id, dataset, replaceChildren() {} };
}

function buildApp({ ajax, overrides = {} } = {}) {
  const nodes = {
    'message-list': node('message-list'),
    'message-list-state': node('message-list-state', { hasMore: 'false', firstUuid: 'm0' }),
    'message-list-items': node('message-list-items'),
  };
  const alerts = [];
  const timers = [];
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/messages.js', {
    document: { getElementById: (id) => nodes[id] || null },
    AppAlert: {
      warning: (message) => alerts.push(['warning', message]),
      error: (message) => alerts.push(['error', message]),
    },
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimeout: () => {},
  });

  const app = ctx.chatMessagesMixin();
  const requests = [];
  Object.assign(app, {
    activeConversation: { uuid: 'c1' },
    $refs: { messagesContainer: { scrollTop: 0, scrollHeight: 100, clientHeight: 0 } },
    $nextTick(fn) { if (fn) fn(); },
    $ajax: ajax || (async (url, options) => { requests.push({ url, options }); return []; }),
    ...overrides,
  });
  return { app, requests, alerts, timers, nodes };
}

// Let every already-resolved promise in the chain run to completion.
async function settle(rounds = 20) {
  for (let i = 0; i < rounds; i += 1) await new Promise((r) => setImmediate(r));
}

// ── Coalescing ─────────────────────────────────────────────

test('a burst of refreshes costs one request in flight and one behind it', async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let requests = 0;
  const { app } = buildApp({
    ajax: async () => {
      requests += 1;
      if (requests === 1) await gate;
      return [];
    },
  });

  const burst = [];
  for (let i = 0; i < 5; i += 1) burst.push(app._refreshCurrentMessages());
  assert.equal(requests, 1, 'only the first of the burst may reach the network');

  release();
  await Promise.all(burst);
  await settle();

  assert.equal(requests, 2, 'the four that queued behind it collapse into one');
});

test('a refresh issued while one is in flight resolves with it', async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let requests = 0;
  const { app } = buildApp({
    ajax: async () => {
      requests += 1;
      if (requests === 1) await gate;
      return [];
    },
  });

  const first = app._refreshCurrentMessages();
  const second = app._refreshCurrentMessages();
  release();
  await Promise.all([first, second]);
  await settle();
  assert.equal(requests, 2);
});

test('refreshes that do not overlap each get their own request', async () => {
  const { app, requests } = buildApp();
  await app._refreshCurrentMessages();
  await app._refreshCurrentMessages();
  await settle();
  assert.equal(requests.length, 2, 'coalescing must not swallow a later, separate refresh');
});

test('a failed refresh still clears the way for the next one', async () => {
  let attempt = 0;
  const { app } = buildApp({
    ajax: async () => {
      attempt += 1;
      if (attempt === 1) throw new Error('network down');
      return [];
    },
  });
  await app._refreshCurrentMessages();
  await app._refreshCurrentMessages();
  await settle();
  assert.equal(attempt, 2, 'a rejection must not wedge the in-flight slot shut');
});

// ── Surfacing a failed swap ────────────────────────────────

function errorEvent({ status, url = 'http://localhost/chat/c1/messages', retryAfter = null }) {
  return {
    detail: {
      ok: false,
      status,
      url,
      headers: { get: (name) => (name.toLowerCase() === 'retry-after' ? retryAfter : null) },
    },
  };
}

test('a rate-limited list says so once and retries after Retry-After', async () => {
  const { app, alerts, timers } = buildApp();
  app.onMessagesAjaxError(errorEvent({ status: 429, retryAfter: '12' }));

  assert.deepStrictEqual(alerts, [['warning', 'Messages are paused for a moment.']]);
  assert.equal(timers.length, 1);
  assert.equal(timers[0].ms, 12000);

  // A second 429 while that retry is pending must not stack a toast or a timer.
  app.onMessagesAjaxError(errorEvent({ status: 429, retryAfter: '12' }));
  assert.equal(alerts.length, 1);
  assert.equal(timers.length, 1);
});

test('a 429 with no Retry-After falls back to five seconds', () => {
  for (const retryAfter of [null, '', 'soon', '0', '-3']) {
    const { app, timers } = buildApp();
    app.onMessagesAjaxError(errorEvent({ status: 429, retryAfter }));
    assert.equal(timers[0].ms, 5000, `Retry-After ${JSON.stringify(retryAfter)}`);
  }
});

test('the scheduled retry runs exactly one refresh and re-arms the handler', async () => {
  const { app, requests, timers, alerts } = buildApp();
  app.onMessagesAjaxError(errorEvent({ status: 429, retryAfter: '1' }));
  assert.equal(requests.length, 0, 'nothing is retried before the delay elapses');

  timers[0].fn();
  await settle();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, '/chat/c1/messages');

  // The outage is over as far as the handler knows, so it may complain again.
  app.onMessagesAjaxError(errorEvent({ status: 429, retryAfter: '1' }));
  assert.equal(alerts.length, 2);
});

test('a non-429 failure is reported and not retried', () => {
  const { app, alerts, timers } = buildApp();
  app.onMessagesAjaxError(errorEvent({ status: 500 }));
  assert.deepStrictEqual(alerts, [['error', 'Could not refresh the messages.']]);
  assert.deepStrictEqual(timers, []);
});

test('failures of other swaps on the same root are none of this handler s business', () => {
  // The event bubbles to the app root, which also issues the conversation
  // list and the thread panel through $ajax.
  const { app, alerts, timers } = buildApp();
  app.onMessagesAjaxError(errorEvent({ status: 500, url: 'http://localhost/chat/conversations' }));
  app.onMessagesAjaxError(errorEvent({ status: 429, url: 'http://localhost/chat/c2/messages' }));
  assert.deepStrictEqual(alerts, []);
  assert.deepStrictEqual(timers, []);
});

test('a paginated list URL is still this surface s', () => {
  const { app, alerts } = buildApp();
  app.onMessagesAjaxError(errorEvent({ status: 500, url: 'http://localhost/chat/c1/messages?before=m9' }));
  assert.equal(alerts.length, 1);
});

test('an error arriving with no conversation open is ignored, not thrown on', () => {
  // The chat page binds this on a root that outlives every conversation, and
  // the sidebar swaps through the same $ajax - so the handler runs with
  // activeConversation still null on a fresh page.
  const { app, alerts, timers } = buildApp();
  app.activeConversation = null;
  app.onMessagesAjaxError(errorEvent({ status: 500, url: 'http://localhost/chat/conversations' }));
  assert.deepStrictEqual(alerts, []);
  assert.deepStrictEqual(timers, []);
});
