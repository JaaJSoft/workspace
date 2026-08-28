'use strict';

// Regression tests for the Alpine `push` store in stores.js.
//
// The backend prunes a PushSubscription row when the push service answers
// 404/410. The browser keeps its local subscription, so the toggle still
// shows "enabled" while the server has nothing to push to - the device goes
// silent forever. init() must therefore re-register the browser's existing
// subscription with the backend on every load (the endpoint upserts, so the
// call is idempotent and cheap).

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function loadPushStore({ subscription = null, fetchOk = true } = {}) {
  const stores = {};
  const fetchCalls = [];
  const warnings = [];
  const Alpine = {
    store(name, obj) {
      if (obj === undefined) return stores[name];
      stores[name] = obj;
      return obj;
    },
  };
  const document = {
    _alpineInit: null,
    addEventListener(type, cb) {
      if (type === 'alpine:init') this._alpineInit = cb;
    },
    body: { dataset: {} },
  };
  const registration = {
    pushManager: {
      getSubscription: async () => subscription,
    },
  };
  const navigator = {
    serviceWorker: {
      register: async () => registration,
      ready: Promise.resolve(registration),
    },
  };
  loadScript('workspace/core/static/core/js/stores.js', {
    Alpine,
    document,
    navigator,
    PushManager: function PushManager() {},
    addEventListener() {},
    getCSRFToken: () => 'csrf',
    console: { ...console, warn: (...args) => warnings.push(args) },
    fetch: async (url, opts) => {
      fetchCalls.push({ url, opts: opts || {} });
      return { ok: fetchOk, status: fetchOk ? 201 : 500, json: async () => ({}) };
    },
  });
  // Fire alpine:init: registers the stores and kicks off push.init().
  document._alpineInit();
  return { stores, fetchCalls, warnings };
}

// init() awaits register() then getSubscription() then the sync fetch; give
// the microtask/macrotask chain a couple of turns to settle.
async function flush() {
  for (let i = 0; i < 4; i++) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

test('init re-registers an existing browser subscription with the backend', async () => {
  const sub = {
    endpoint: 'https://push.example.com/sub/1',
    toJSON() {
      return { endpoint: this.endpoint, keys: { p256dh: 'p', auth: 'a' } };
    },
  };
  const { stores, fetchCalls } = loadPushStore({ subscription: sub });
  await flush();

  assert.equal(stores.push.enabled, true);
  const sync = fetchCalls.find(
    (c) => c.url === '/api/v1/notifications/push/subscribe' && c.opts.method === 'POST'
  );
  assert.ok(sync, 'expected a POST /push/subscribe resync on init');
  const body = JSON.parse(sync.opts.body);
  assert.equal(body.endpoint, 'https://push.example.com/sub/1');
  assert.deepStrictEqual({ ...body.keys }, { p256dh: 'p', auth: 'a' });
});

test('a failed resync is surfaced as a warning, not swallowed', async () => {
  const sub = {
    endpoint: 'https://push.example.com/sub/1',
    toJSON() {
      return { endpoint: this.endpoint, keys: { p256dh: 'p', auth: 'a' } };
    },
  };
  const { warnings } = loadPushStore({ subscription: sub, fetchOk: false });
  await flush();

  const surfaced = warnings.find((args) => args.join(' ').includes('resync'));
  assert.ok(surfaced, 'expected a console.warn mentioning the failed resync');
});

test('init without an existing subscription does not call the backend', async () => {
  const { stores, fetchCalls } = loadPushStore({ subscription: null });
  await flush();

  assert.equal(stores.push.enabled, false);
  assert.equal(fetchCalls.length, 0);
});
