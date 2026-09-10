'use strict';

// The stream's resume point has to survive a reconnect the page drives itself.
//
// EventSource replays the last event id on the reconnects it performs on its
// own, but connect() builds a brand new EventSource on a visibility resume or
// a bfcache restore - and a fresh one has no id to replay. Without the cursor
// in the URL those streams silently restart from the head and whatever was
// queued while they were down is never delivered.

const assert = require('node:assert');
const { test } = require('node:test');

const { loadScript } = require('../../../common/tests/js/loader');

function loadSse() {
  const instances = [];
  const windowHandlers = {};
  const documentHandlers = {};

  class FakeEventSource {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 2;

    constructor(url) {
      this.url = url;
      this.readyState = FakeEventSource.OPEN;
      this.listeners = {};
      instances.push(this);
    }

    addEventListener(type, handler) {
      this.listeners[type] = handler;
    }

    close() {
      this.readyState = FakeEventSource.CLOSED;
    }

    deliver(data, lastEventId) {
      this.listeners.sse({ data: JSON.stringify(data), lastEventId });
    }
  }

  loadScript('workspace/core/static/core/js/sse.js', {
    EventSource: FakeEventSource,
    CustomEvent: class {
      constructor(type, init) {
        this.type = type;
        this.detail = init && init.detail;
      }
    },
    document: {
      hidden: false,
      getElementById: () => null,
      addEventListener: (type, handler) => {
        documentHandlers[type] = handler;
      },
    },
    addEventListener: (type, handler) => {
      windowHandlers[type] = handler;
    },
    dispatchEvent: () => {},
    setTimeout: () => 0,
    clearTimeout: () => {},
  });

  instances[0].onopen();

  return { instances, windowHandlers, documentHandlers };
}

test('a page-driven reconnect carries the last event id it received', () => {
  const { instances, windowHandlers } = loadSse();

  assert.equal(instances[0].url, '/api/v1/stream');
  instances[0].deliver({ event: 'files.file_changed', data: {} }, 'cursor-7');

  windowHandlers.pageshow({ persisted: true });

  assert.equal(
    instances[1].url,
    '/api/v1/stream?last_event_id=cursor-7',
    'the reopened stream must resume where the frozen one stopped'
  );
});

test('the cursor advances with every id-carrying event', () => {
  const { instances, documentHandlers } = loadSse();

  instances[0].deliver({ event: 'files.file_changed', data: {} }, 'cursor-7');
  instances[0].deliver({ event: 'chat.typing', data: {} }, '');
  instances[0].deliver({ event: 'imports.job', data: {} }, 'cursor-9');

  instances[0].readyState = 2; // CLOSED, so visibilitychange reconnects
  documentHandlers.visibilitychange();

  assert.equal(instances[1].url, '/api/v1/stream?last_event_id=cursor-9');
});

test('an id needing escaping does not break the query string', () => {
  const { instances, windowHandlers } = loadSse();

  instances[0].deliver({ event: 'files.file_changed', data: {} }, 'a&b=c d');
  windowHandlers.pageshow({ persisted: true });

  assert.equal(instances[1].url, '/api/v1/stream?last_event_id=a%26b%3Dc%20d');
});
