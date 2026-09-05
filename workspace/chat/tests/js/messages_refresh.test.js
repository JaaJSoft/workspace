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
  const clock = { now: 1000000 };
  const nodes = {
    'message-list': node('message-list'),
    'message-list-state': node('message-list-state', { hasMore: 'false', firstUuid: 'm0' }),
    'message-list-items': node('message-list-items'),
  };
  const alerts = [];
  const timers = [];
  const cleared = [];
  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/messages.js', {
    document: { getElementById: (id) => nodes[id] || null },
    AppAlert: {
      warning: (message) => alerts.push(['warning', message]),
      error: (message) => alerts.push(['error', message]),
    },
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimeout: (id) => { cleared.push(id); },
    Date: { now: () => clock.now },
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
  return { app, requests, alerts, timers, cleared, clock, nodes };
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

// ── The failure the pane actually meets ────────────────────

// alpine-ajax's RenderError is a DOMException carrying the status in its
// MESSAGE and nothing else - no status property, no headers. It is thrown
// when the response lacks the target and nothing cancelled the removal,
// which is what a 429 looks like from inside $ajax.
function renderError(status) {
  const error = new Error(`Target [#message-list] was not found in response with status [${status}].`);
  error.name = 'RenderError';
  return error;
}

test('a throttled swap that rejects inside $ajax is reported, not just logged', async () => {
  const { app, alerts, timers } = buildApp({ ajax: async () => { throw renderError(429); } });
  await app._refreshCurrentMessages();
  await settle();

  assert.deepStrictEqual(alerts, [['warning', 'Messages are paused for a moment.']]);
  assert.equal(timers.length, 1);
  assert.equal(timers[0].ms, 5000, 'the RenderError carries no Retry-After, so the fallback applies');
});

test('a 5xx that rejects inside $ajax is reported too', async () => {
  const { app, alerts, timers } = buildApp({ ajax: async () => { throw renderError(503); } });
  await app._refreshCurrentMessages();
  await settle();
  assert.deepStrictEqual(alerts, [['error', 'Could not refresh the messages.']]);
  assert.deepStrictEqual(timers, []);
});

test('a plain transport failure is logged, not dressed up as a status', async () => {
  const { app, alerts } = buildApp({ ajax: async () => { throw new Error('network down'); } });
  await app._refreshCurrentMessages();
  await settle();
  assert.deepStrictEqual(alerts, [], 'no status to report and nothing to retry against');
});

test('a missing target carrying a non-2xx is reported before the removal is cancelled', () => {
  // The path where our own guard DOES cancel: nothing throws, $ajax resolves
  // with nothing merged, and this event is the only place the status shows.
  const { app, alerts, timers } = buildApp();
  const event = {
    detail: {
      target: { closest: (sel) => (sel === '#messages-container' ? {} : null) },
      response: {
        ok: false,
        status: 429,
        url: 'http://localhost/chat/c1/messages',
        headers: { get: (n) => (n.toLowerCase() === 'retry-after' ? '7' : null) },
      },
    },
    prevented: false,
    preventDefault() { this.prevented = true; },
  };

  app._onAjaxMissing(event);

  assert.equal(event.prevented, true, 'the live list must still be kept');
  assert.deepStrictEqual(alerts, [['warning', 'Messages are paused for a moment.']]);
  assert.equal(timers[0].ms, 7000, 'this path DOES have the header');
});

test('a missing target on a healthy response reports nothing', () => {
  const { app, alerts } = buildApp();
  const event = {
    detail: {
      target: { closest: () => ({}) },
      response: { ok: true, status: 200, url: 'http://localhost/chat/c1/messages' },
    },
    preventDefault() {},
  };
  app._onAjaxMissing(event);
  assert.deepStrictEqual(alerts, []);
});

// ── Teardown mid-drain ─────────────────────────────────────

test('a surface torn down mid-refresh does not issue the request queued behind it', async () => {
  // The exact race _dead was written for: opening thread B while thread A is
  // still loading keeps the same target ids, so a request issued by A's dead
  // component would merge A into B.
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let requests = 0;
  let dead = false;
  const { app } = buildApp({
    ajax: async () => {
      requests += 1;
      if (requests === 1) await gate;
      return [];
    },
  });
  app._surfaceGone = () => dead;

  const first = app._refreshCurrentMessages();
  app._refreshCurrentMessages();
  assert.equal(requests, 1);

  dead = true;
  release();
  await first;
  await settle();

  assert.equal(requests, 1, 'the queued refresh must die with the surface');
});

test('a surface torn down before the drain even starts issues nothing', async () => {
  const { app, requests } = buildApp();
  app._surfaceGone = () => true;
  await app._refreshCurrentMessages();
  await settle();
  assert.deepStrictEqual(requests, []);
});

// ── Toast storms ───────────────────────────────────────────

test('a sustained 5xx under steady traffic is one toast, not one per round-trip', async () => {
  const { app, alerts, clock } = buildApp({ ajax: async () => { throw renderError(500); } });

  for (let i = 0; i < 6; i += 1) {
    await app._refreshCurrentMessages();
    await settle(3);
    clock.now += 1000;
  }
  assert.equal(alerts.length, 1, 'six failures inside the window are one complaint');

  clock.now += 10000;
  await app._refreshCurrentMessages();
  await settle();
  assert.equal(alerts.length, 2, 'once the window has passed, it may say so again');
});

// ── Teardown releases the retry ────────────────────────────

test('cancelling the retry clears the pending timer and re-arms reporting', () => {
  const { app, alerts, timers, cleared } = buildApp();
  app.onMessagesAjaxError(errorEvent({ status: 429, retryAfter: '30' }));
  assert.equal(timers.length, 1);

  app._cancelMessagesRetry();
  assert.deepStrictEqual(cleared, [1]);

  app.onMessagesAjaxError(errorEvent({ status: 429, retryAfter: '30' }));
  assert.equal(alerts.length, 2, 'nothing is pending any more, so a fresh outage is reported');
});

test('the thread panel matches its own list URL, not the conversation s', () => {
  // Same handler, third surface: the panel overrides _messagesUrl, so the
  // guard follows it without knowing anything about threads.
  const { app, alerts } = buildApp({
    overrides: { _messagesUrl: (cursor) => `/chat/threads/r1/messages${cursor ? '?before=' + cursor : ''}` },
  });
  app.onMessagesAjaxError(errorEvent({ status: 500, url: 'http://localhost/chat/c1/messages' }));
  assert.deepStrictEqual(alerts, [], 'the conversation behind the panel is not the panel');

  app.onMessagesAjaxError(errorEvent({ status: 500, url: 'http://localhost/chat/threads/r1/messages' }));
  assert.equal(alerts.length, 1);
});

test('repeated throttling backs off instead of spending the bucket it waits for', async () => {
  // The retry is itself a request against the same per-minute bucket, so a
  // flat delay keeps the bucket at zero and the outage never ends.
  const { app, timers, clock } = buildApp({ ajax: async () => { throw renderError(429); } });

  await app._refreshCurrentMessages();
  await settle();
  assert.equal(timers[0].ms, 5000);

  for (let round = 1; round < 5; round += 1) {
    clock.now += 60000;
    timers[timers.length - 1].fn();
    await settle();
  }
  assert.deepStrictEqual(timers.map((t) => t.ms), [5000, 10000, 20000, 40000, 60000],
    'doubling, capped at the length of the window being waited out');
});

test('a Retry-After the server actually sent wins over the backoff', () => {
  const { app, timers } = buildApp();
  app.onMessagesAjaxError(errorEvent({ status: 429, retryAfter: '3' }));
  assert.equal(timers[0].ms, 3000);
});

test('a list that comes back forgets the backoff', async () => {
  let fail = true;
  const { app, timers } = buildApp({
    ajax: async () => {
      if (fail) throw renderError(429);
      return [];
    },
  });

  await app._refreshCurrentMessages();
  await settle();
  assert.equal(timers[0].ms, 5000);

  fail = false;
  timers[0].fn();
  await settle();

  fail = true;
  await app._refreshCurrentMessages();
  await settle();
  assert.equal(timers[timers.length - 1].ms, 5000, 'a recovery resets the ladder');
});
