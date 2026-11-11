'use strict';

// A share from another app reaches the installed app as a multipart POST to
// the manifest's share_target action. The service worker keeps the files in
// the browser and sends the app to the Files page with their id.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

const ORIGIN = 'https://example.test';

function loadServiceWorker({ put = async () => 'share-1' } = {}) {
  const listeners = {};
  const imported = [];
  const stored = [];
  const self = {
    location: new URL(`${ORIGIN}/sw.js?v=1.2.3`),
    addEventListener(type, fn) { listeners[type] = fn; },
    shareTargetInbox: {
      put: async (files) => { stored.push(files); return put(files); },
    },
  };
  loadScript('workspace/core/static/sw.js', {
    URL,
    Response,
    console: { warn() {} },
    importScripts: (url) => imported.push(url),
    self,
  });
  return { listeners, imported, stored };
}

function shareRequest(form, path = '/files/share-target') {
  return new Request(`${ORIGIN}${path}`, { method: 'POST', body: form });
}

function dispatchFetch(listeners, request) {
  let responded = null;
  listeners.fetch({ request, respondWith: (response) => { responded = response; } });
  return responded;
}

test('the inbox is imported at the cache version, so a release refreshes it', () => {
  const { imported } = loadServiceWorker();
  assert.deepStrictEqual(imported, ['/static/files/ui/js/share_target_inbox.js?v=1.2.3']);
});

test('shared files are kept and the app opens the Files page on their id', async () => {
  const { listeners, stored } = loadServiceWorker();
  const form = new FormData();
  form.append('files', new File(['a'], 'a.jpg', { type: 'image/jpeg' }));
  form.append('files', new File(['bb'], 'b.pdf', { type: 'application/pdf' }));

  const response = await dispatchFetch(listeners, shareRequest(form));

  assert.equal(response.status, 303);
  assert.equal(response.headers.get('Location'), `${ORIGIN}/files?share_target=share-1`);
  assert.equal(stored.length, 1);
  assert.deepStrictEqual(Array.from(stored[0], (file) => file.name), ['a.jpg', 'b.pdf']);
});

test('a share without files lands on the Files page with an empty id', async () => {
  const { listeners, stored } = loadServiceWorker();
  const form = new FormData();
  form.append('files', 'not a file');

  const response = await dispatchFetch(listeners, shareRequest(form));

  assert.equal(response.headers.get('Location'), `${ORIGIN}/files?share_target=`);
  assert.equal(stored.length, 0);
});

test('an inbox that cannot store the files still sends the app to the Files page', async () => {
  const { listeners } = loadServiceWorker({ put: async () => { throw new Error('quota'); } });
  const form = new FormData();
  form.append('files', new File(['a'], 'a.jpg'));

  const response = await dispatchFetch(listeners, shareRequest(form));

  assert.equal(response.headers.get('Location'), `${ORIGIN}/files?share_target=`);
});

test('other POST requests go to the network', () => {
  const { listeners } = loadServiceWorker();
  assert.equal(dispatchFetch(listeners, shareRequest(new FormData(), '/api/v1/files')), null);
});
