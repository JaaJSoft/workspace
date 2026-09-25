'use strict';

// A tab left open on an idle PC keeps making requests on its own. Those must
// not count as presence activity, or web push to the user's other devices is
// held back as if they were sitting in front of it.

const assert = require('node:assert');
const { test } = require('node:test');

const { loadScript } = require('../../../common/tests/js/loader');

function loadAttention({ visibility = 'visible', focused = true } = {}) {
  const page = { visibility, focused };
  const windowHandlers = {};
  const documentHandlers = {};
  const dispatched = [];
  const fetched = [];
  const clock = { now: 1_000_000_000 };

  const ctx = loadScript('workspace/core/static/core/js/page_attention.js', {
    Date: { now: () => clock.now },
    Headers,
    Request,
    URL,
    CustomEvent: class {
      constructor(type) { this.type = type; }
    },
    location: { href: 'https://ws.example/chat', origin: 'https://ws.example' },
    document: {
      get visibilityState() { return page.visibility; },
      hasFocus: () => page.focused,
      addEventListener: (type, handler) => { documentHandlers[type] = handler; },
    },
    addEventListener: (type, handler) => { windowHandlers[type] = handler; },
    dispatchEvent: (event) => dispatched.push(event.type),
    fetch: async (input, init) => { fetched.push({ input, init }); return { ok: true }; },
  });

  // Nobody touches the page for ten minutes.
  function goIdle() {
    clock.now += 10 * 60 * 1000;
  }

  return { ctx, page, windowHandlers, documentHandlers, dispatched, fetched, goIdle };
}

function unattendedHeader(call) {
  return new Headers(call.init?.headers).get('X-Page-Unattended');
}

test('a visible, focused page with recent input is attended', () => {
  const { ctx } = loadAttention();
  assert.equal(ctx.pageAttention.isAttended(), true);
});

test('a hidden tab, an unfocused window or an idle page is not attended', () => {
  assert.equal(loadAttention({ visibility: 'hidden' }).ctx.pageAttention.isAttended(), false);
  assert.equal(loadAttention({ focused: false }).ctx.pageAttention.isAttended(), false);

  const idle = loadAttention();
  idle.goIdle();
  assert.equal(idle.ctx.pageAttention.isAttended(), false);
});

test('requests from an attended page go out untouched', async () => {
  const { ctx, fetched } = loadAttention();

  await ctx.fetch('/chat/conversations/items?uuids=a');

  assert.equal(fetched.length, 1);
  assert.equal(unattendedHeader(fetched[0]), null);
});

test('same-origin requests from an unattended page are flagged', async () => {
  const { ctx, fetched } = loadAttention({ visibility: 'hidden' });

  await ctx.fetch('/chat/conversations/items?uuids=a', {
    headers: { 'X-Alpine-Request': 'true' },
  });
  await ctx.fetch('https://ws.example/api/v1/notifications');

  assert.equal(unattendedHeader(fetched[0]), '1');
  assert.equal(new Headers(fetched[0].init.headers).get('X-Alpine-Request'), 'true',
    'the caller\'s own headers are kept');
  assert.equal(unattendedHeader(fetched[1]), '1');
});

test('a Request object keeps its own headers when flagged', async () => {
  const { ctx, fetched } = loadAttention({ focused: false });

  await ctx.fetch(new Request('https://ws.example/api/v1/x', { headers: { 'X-CSRFToken': 't' } }));

  const headers = new Headers(fetched[0].init.headers);
  assert.equal(headers.get('X-Page-Unattended'), '1');
  assert.equal(headers.get('X-CSRFToken'), 't');
});

test('cross-origin requests are never flagged', async () => {
  const { ctx, fetched } = loadAttention({ visibility: 'hidden' });

  await ctx.fetch('https://other.example/resource');

  assert.equal(fetched[0].init, undefined, 'no header, so no CORS preflight');
});

test('the first input after being idle announces the return', () => {
  const { windowHandlers, dispatched, goIdle } = loadAttention();
  goIdle();
  dispatched.length = 0;

  windowHandlers.keydown();
  windowHandlers.keydown();

  assert.deepStrictEqual(dispatched, ['page:attended'], 'once, not on every key');
});

test('showing the tab again announces the return', () => {
  const { page, documentHandlers, dispatched } = loadAttention({ visibility: 'hidden' });

  documentHandlers.visibilitychange();
  assert.deepStrictEqual(dispatched, [], 'still hidden');

  page.visibility = 'visible';
  documentHandlers.visibilitychange();
  assert.deepStrictEqual(dispatched, ['page:attended']);
});
