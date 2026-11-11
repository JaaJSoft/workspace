'use strict';

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// sw.js reads self.location at module scope, imports the share target inbox
// and registers its listeners at load time. URL is a Node global rather than
// an ECMAScript intrinsic, so a bare vm context does not have it.
function loadServiceWorker() {
  return loadScript('workspace/core/static/sw.js', {
    URL,
    importScripts() {},
    self: {
      location: 'https://example.test/sw.js?v=test',
      addEventListener() {},
    },
  });
}

test('the payload tag wins over the origin', () => {
  const { buildNotificationOptions } = loadServiceWorker();
  const options = buildNotificationOptions({ origin: 'chat', tag: 'conversation_id:42' });
  assert.equal(options.tag, 'conversation_id:42');
});

test('two payloads with different source tags do not share a tag', () => {
  const { buildNotificationOptions } = loadServiceWorker();
  const first = buildNotificationOptions({ origin: 'chat', tag: 'conversation_id:1' });
  const second = buildNotificationOptions({ origin: 'chat', tag: 'conversation_id:2' });
  assert.notEqual(first.tag, second.tag);
});

test('a payload without a tag falls back to the origin', () => {
  const { buildNotificationOptions } = loadServiceWorker();
  assert.equal(buildNotificationOptions({ origin: 'mail' }).tag, 'mail');
});

test('a payload with neither tag nor origin still has a tag', () => {
  const { buildNotificationOptions } = loadServiceWorker();
  assert.equal(buildNotificationOptions({}).tag, 'workspace');
});

test('the url defaults to the root', () => {
  const { buildNotificationOptions } = loadServiceWorker();
  assert.equal(buildNotificationOptions({}).data.url, '/');
});

// Fires the worker's install listener and reports what it cached, and where.
async function installedCache(swUrl) {
  const listeners = {};
  const opened = [];
  loadScript('workspace/core/static/sw.js', {
    URL,
    importScripts() {},
    self: {
      location: swUrl,
      addEventListener(type, listener) { listeners[type] = listener; },
      skipWaiting() {},
    },
    caches: {
      open(name) {
        const cache = { name, urls: [], addAll(urls) { cache.urls = urls; return Promise.resolve(); } };
        opened.push(cache);
        return Promise.resolve(cache);
      },
    },
  });
  let pending;
  listeners.install({ waitUntil(promise) { pending = promise; } });
  await pending;
  return opened[0];
}

test('the offline page is cached under the language of the worker url', async () => {
  const french = await installedCache('https://example.test/sw.js?v=test&lang=fr');
  const english = await installedCache('https://example.test/sw.js?v=test&lang=en');
  assert.equal(french.name, 'workspace-test-fr');
  assert.equal(english.name, 'workspace-test-en');
  assert.ok(french.urls.includes('/offline'));
});

test('a push without a JSON payload is titled with the product name', async () => {
  const listeners = {};
  const shown = [];
  loadScript('workspace/core/static/sw.js', {
    URL,
    importScripts() {},
    self: {
      location: 'https://example.test/sw.js?v=test',
      addEventListener(type, listener) { listeners[type] = listener; },
      registration: { showNotification(title, options) { shown.push({ title, options }); } },
    },
  });
  listeners.push({
    data: { json() { throw new SyntaxError('not JSON'); }, text() { return 'Hello'; } },
    waitUntil() {},
  });
  assert.equal(shown[0].title, 'Workspace');
  assert.equal(shown[0].options.body, 'Hello');
});
