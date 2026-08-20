'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { loadScript } = require('../../../common/tests/js/loader');

/**
 * Load chat_preferences.js with fetch and the event bus stubbed.
 *
 * The cache hydrates synchronously from the server-embedded json_script
 * (absent here, so defaults apply); the tests look at the PUTs only.
 */
function load({ putResolves = true, putOk = true } = {}) {
  const calls = [];
  const dispatched = [];
  let releasePut;
  const putSettled = new Promise((resolve) => {
    releasePut = resolve;
  });

  const ctx = loadScript('workspace/chat/ui/static/chat/ui/js/chat_preferences.js', {
    getCSRFToken: () => 'csrf-token',
    setTimeout,
    clearTimeout,
    fetch: async (url, opts = {}) => {
      calls.push({ url, method: opts.method || 'GET', body: opts.body });
      if ((opts.method || 'GET') === 'PUT') {
        if (!putResolves) throw new Error('offline');
        releasePut();
        return { ok: putOk, status: putOk ? 200 : 500, json: async () => ({ value: {} }) };
      }
      return { ok: true, json: async () => ({ value: {} }) };
    },
    // The factory reads the call-sounds seed at construction, and every write
    // broadcasts a change event.
    document: { getElementById: () => null },
    CustomEvent: class {
      constructor(type, init) {
        this.type = type;
        this.detail = init?.detail;
      }
    },
    dispatchEvent: (e) => dispatched.push(e.type),
  });

  const app = ctx.chatPreferences();
  app.prefs = { compactMessageView: false, showThreadRepliesInline: false };
  return {
    app,
    puts: () => calls.filter((c) => c.method === 'PUT'),
    refreshes: () => dispatched.filter((t) => t === 'chat:refresh-messages'),
    putSettled,
  };
}

test('a server-applied preference is written immediately, not debounced', () => {
  // Regression: the toggle went through update(), whose write is debounced
  // 500ms, so a refetch issued right after came back filtered by the old
  // value - and when the handler reloaded the page instead, the queued timer
  // died with it and the preference never reached the server at all.
  const h = load();

  h.app.updateAndSync('showThreadRepliesInline', true);

  const puts = h.puts();
  assert.equal(puts.length, 1, 'the write goes out on the spot');
  assert.equal(JSON.parse(puts[0].body).value.showThreadRepliesInline, true);
});

test('the message list is refetched only after the write lands', async () => {
  const h = load();

  const pending = h.app.updateAndSync('showThreadRepliesInline', true);
  await h.putSettled;
  // The refresh must not have been dispatched before the PUT settled.
  const refreshesAtPutTime = h.refreshes().length;
  await pending;

  assert.equal(refreshesAtPutTime, 0, 'no refetch races ahead of the write');
  assert.deepStrictEqual(h.refreshes(), ['chat:refresh-messages']);
});

test('the list is refreshed in place rather than by reloading the page', async () => {
  const h = load();
  await h.app.updateAndSync('showThreadRepliesInline', true);
  assert.deepStrictEqual(
    h.refreshes(),
    ['chat:refresh-messages'],
    'chatApp already listens for this and does an incremental refresh',
  );
});

test('a failed write skips the refetch, leaving the list as the server has it', async () => {
  const h = load({ putResolves: false });

  await h.app.updateAndSync('showThreadRepliesInline', true);

  assert.deepStrictEqual(h.refreshes(), []);
});

test('an http error response skips the refetch too', async () => {
  // fetch resolves on 4xx/5xx, so a rejected promise is not the only failure
  // shape: a 500 also means the server kept the old value, and refetching
  // would repaint the list filtered by a preference that was never saved.
  const h = load({ putOk: false });

  await h.app.updateAndSync('showThreadRepliesInline', true);

  assert.deepStrictEqual(h.refreshes(), []);
});

test('the ordinary update path stays debounced and refreshes nothing', () => {
  const h = load();
  h.app.update('compactMessageView', true);
  assert.equal(h.puts().length, 0, 'still debounced, nothing sent yet');
  assert.deepStrictEqual(h.refreshes(), []);
});
