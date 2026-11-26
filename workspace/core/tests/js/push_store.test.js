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

function loadPushStore({ subscription = null, fetchOk = true, saveOk = () => fetchOk, appVersion = '', language = 'en' } = {}) {
  const stores = {};
  const fetchCalls = [];
  const warnings = [];
  const registered = [];
  const Alpine = {
    // As Alpine does: registering a store that defines init() runs it.
    store(name, obj) {
      if (obj === undefined) return stores[name];
      stores[name] = obj;
      if (Object.hasOwn(obj, 'init') && typeof obj.init === 'function') obj.init();
      return undefined;
    },
  };
  const document = {
    _alpineInit: null,
    addEventListener(type, cb) {
      if (type === 'alpine:init') this._alpineInit = cb;
    },
    body: { dataset: appVersion ? { appVersion } : {} },
    documentElement: { lang: language },
  };
  const registration = {
    pushManager: {
      getSubscription: async () => subscription,
    },
  };
  const navigator = {
    serviceWorker: {
      register: async (url) => {
        registered.push(url);
        return registration;
      },
      ready: Promise.resolve(registration),
    },
  };
  loadScript('workspace/core/static/core/js/stores.js', {
    Alpine,
    document,
    navigator,
    PushManager: function PushManager() {},
    // A Node global rather than an ECMAScript intrinsic: a bare vm context lacks it.
    URLSearchParams,
    addEventListener() {},
    getCSRFToken: () => 'csrf',
    console: { ...console, warn: (...args) => warnings.push(args) },
    fetch: async (url, opts) => {
      fetchCalls.push({ url, opts: opts || {} });
      const ok = (opts || {}).method === 'POST' ? saveOk() : fetchOk;
      return { ok, status: ok ? 201 : 500, json: async () => ({ uuid: 'sub-uuid' }) };
    },
  });
  // Fire alpine:init: registers the stores, which runs push.init().
  document._alpineInit();
  return { stores, fetchCalls, warnings, registered };
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
    (c) => c.url === '/api/v1/notifications/push/subscriptions' && c.opts.method === 'POST'
  );
  assert.ok(sync, 'expected a POST /push/subscriptions resync on init');
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

test('the worker url carries the page language, so a new language reinstalls it', async () => {
  const { registered } = loadPushStore({ appVersion: '1.2.0', language: 'fr' });
  await flush();

  assert.deepStrictEqual([...registered], ['/sw.js?v=1.2.0&lang=fr']);
});

function browserSubscription() {
  return {
    endpoint: 'https://push.example.com/sub/1',
    unsubscribed: false,
    toJSON() {
      return { endpoint: this.endpoint, keys: { p256dh: 'p', auth: 'a' } };
    },
    async unsubscribe() {
      this.unsubscribed = true;
    },
  };
}

test('turning push off deletes the row the server answered on save', async () => {
  const sub = browserSubscription();
  const { stores, fetchCalls } = loadPushStore({ subscription: sub });
  await flush();

  await stores.push.toggle();

  assert.equal(sub.unsubscribed, true);
  assert.equal(stores.push.enabled, false);
  const removal = fetchCalls.find((c) => c.opts.method === 'DELETE');
  assert.equal(removal.url, '/api/v1/notifications/push/subscriptions/sub-uuid');
  assert.equal(removal.opts.body, undefined);
});

test('turning push off after a failed save on load asks the server for its row first', async () => {
  const sub = browserSubscription();
  let saves = 0;
  // The save on load fails, the one made before turning off goes through.
  const { stores, fetchCalls } = loadPushStore({ subscription: sub, saveOk: () => ++saves > 1 });
  await flush();
  assert.equal(stores.push.subscriptionUuid, null);

  await stores.push.toggle();

  assert.deepStrictEqual(
    fetchCalls.map((c) => c.opts.method),
    ['POST', 'POST', 'DELETE']
  );
  assert.equal(fetchCalls[2].url, '/api/v1/notifications/push/subscriptions/sub-uuid');
});

test('turning push off still unsubscribes the browser when the server cannot be reached', async () => {
  const sub = browserSubscription();
  const { stores, fetchCalls } = loadPushStore({ subscription: sub, saveOk: () => false });
  await flush();

  await stores.push.toggle();

  assert.equal(sub.unsubscribed, true);
  assert.equal(stores.push.enabled, false);
  assert.equal(fetchCalls.some((c) => c.opts.method === 'DELETE'), false);
});
