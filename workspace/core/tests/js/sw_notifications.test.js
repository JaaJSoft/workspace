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
