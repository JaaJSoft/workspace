'use strict';

// Regression tests for the bfcache (back/forward cache) resume path.
//
// On a mobile back gesture the browser restores a frozen page from memory:
// no script re-runs and no request goes out, so every module keeps rendering
// the data it held before the freeze. The visibilitychange handler alone does
// not save us - it only reconnects when readyState is CLOSED, and a stream
// frozen by the bfcache can still report OPEN while its socket is dead.

const assert = require('node:assert');
const { test } = require('node:test');

const { loadScript } = require('../../../common/tests/js/loader');

function loadSse({ hidden = false } = {}) {
  const instances = [];
  const dispatched = [];
  const windowHandlers = {};
  const documentHandlers = {};

  class FakeEventSource {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 2;

    constructor(url) {
      this.url = url;
      this.readyState = FakeEventSource.OPEN;
      this.closed = false;
      instances.push(this);
    }

    addEventListener() {}

    close() {
      this.closed = true;
      this.readyState = FakeEventSource.CLOSED;
    }
  }

  loadScript('workspace/common/static/ui/js/sse.js', {
    EventSource: FakeEventSource,
    CustomEvent: class {
      constructor(type, init) {
        this.type = type;
        this.detail = init && init.detail;
      }
    },
    document: {
      hidden,
      getElementById: () => null,
      addEventListener: (type, handler) => {
        documentHandlers[type] = handler;
      },
    },
    addEventListener: (type, handler) => {
      windowHandlers[type] = handler;
    },
    dispatchEvent: (event) => dispatched.push(event.type),
    setTimeout: () => 0,
    clearTimeout: () => {},
  });

  // The very first onopen is intentionally silent (initial state comes from
  // the server-rendered template). Fire it so the context matches a page that
  // has been up and running before being frozen.
  instances[0].onopen();
  dispatched.length = 0;

  return { instances, dispatched, windowHandlers, documentHandlers };
}

test('the initial connect happens at load and stays silent', () => {
  const { instances, dispatched } = loadSse();

  assert.equal(instances.length, 1);
  assert.equal(instances[0].url, '/api/v1/stream');
  assert.deepStrictEqual(dispatched, [], 'first connect must not fire sse:reconnect');
});

test('a bfcache restore reconnects even when the dead stream still reports OPEN', () => {
  const { instances, dispatched, windowHandlers } = loadSse();

  // This is the exact bfcache shape: the page was frozen mid-stream, so the
  // EventSource object still claims OPEN although nothing flows through it.
  assert.equal(instances[0].readyState, 1);

  windowHandlers.pageshow({ persisted: true });

  assert.equal(instances.length, 2, 'a bfcache restore must open a fresh stream');
  assert.equal(instances[0].closed, true, 'the stale stream must be closed');

  instances[1].onopen();
  assert.deepStrictEqual(
    dispatched,
    ['sse:reconnect'],
    'listeners must be told to re-sync after a bfcache restore'
  );
});

test('a normal load fires pageshow without persisted and must not reconnect', () => {
  const { instances, windowHandlers } = loadSse();

  windowHandlers.pageshow({ persisted: false });

  assert.equal(instances.length, 1, 'a non-bfcache pageshow must not churn the stream');
});

test('visibilitychange alone cannot cover the bfcache case', () => {
  const { instances, documentHandlers } = loadSse();

  // Same starting point as the bfcache test: OPEN-but-dead.
  documentHandlers.visibilitychange();

  assert.equal(
    instances.length,
    1,
    'readyState is OPEN so this handler skips - which is why pageshow is needed'
  );
});

test('visibilitychange still reconnects a stream that closed itself', () => {
  const { instances, documentHandlers } = loadSse();

  instances[0].readyState = 2; // CLOSED

  documentHandlers.visibilitychange();

  assert.equal(instances.length, 2, 'the pre-existing resume path must keep working');
});
