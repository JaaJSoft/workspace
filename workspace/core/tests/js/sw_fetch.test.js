'use strict';

// Which requests the service worker answers itself, and which it leaves to
// the browser.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const ORIGIN = 'https://example.test';
const FORECAST = 'https://api.open-meteo.com/v1/forecast?latitude=48.86&longitude=2.35';

/**
 * The worker's fetch listener, over a cache holding `cached` (url -> body)
 * and a network answering every request with `fresh`.
 */
function loadServiceWorker({ cached = {}, fresh = 'fresh' } = {}) {
  const listeners = {};
  const stored = [];
  loadScript('workspace/core/static/sw.js', {
    URL,
    Response,
    importScripts() {},
    self: {
      location: new URL(`${ORIGIN}/sw.js?v=1.2.3`),
      addEventListener(type, fn) { listeners[type] = fn; },
    },
    caches: {
      match: async (request) => (request.url in cached ? new Response(cached[request.url]) : undefined),
      open: async () => ({ put: async (request) => { stored.push(request.url); } }),
    },
    fetch: async () => new Response(fresh),
  });
  return { listeners, stored };
}

function dispatchFetch(listeners, request) {
  let responded = null;
  listeners.fetch({ request, respondWith: (response) => { responded = response; } });
  return responded;
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

test('a request to another origin is left to the browser, even with a copy in the cache', async () => {
  const { listeners, stored } = loadServiceWorker({ cached: { [FORECAST]: 'the previous visit' } });

  const responded = dispatchFetch(listeners, new Request(FORECAST));
  await settle();

  assert.equal(responded, null);
  assert.deepStrictEqual(stored, []);
});

test('a page of the app is answered by the worker, from the network', async () => {
  const { listeners } = loadServiceWorker();

  const responded = dispatchFetch(listeners, { method: 'GET', url: `${ORIGIN}/files`, mode: 'navigate' });

  assert.equal(await (await responded).text(), 'fresh');
});
