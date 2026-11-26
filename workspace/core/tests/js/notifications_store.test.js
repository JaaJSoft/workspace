'use strict';

// The Alpine `notifications` store in stores.js: what it asks the API for.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function loadNotificationsStore(pages) {
  const stores = {};
  const fetchCalls = [];
  const Alpine = {
    store(name, obj) {
      if (obj === undefined) return stores[name];
      stores[name] = obj;
      return undefined;
    },
  };
  const document = {
    _alpineInit: null,
    addEventListener(type, cb) {
      if (type === 'alpine:init') this._alpineInit = cb;
    },
    body: { dataset: {} },
    documentElement: { lang: 'en' },
  };
  loadScript('workspace/core/static/core/js/stores.js', {
    Alpine,
    document,
    navigator: {},
    URLSearchParams,
    addEventListener() {},
    getCSRFToken: () => 'csrf',
    fetch: async (url, opts) => {
      fetchCalls.push({ url, opts: opts || {} });
      const page = pages.shift() || { rows: [], hasMore: false };
      return {
        ok: true,
        status: 200,
        headers: { get: (name) => (name === 'X-Has-More' ? String(page.hasMore) : null) },
        json: async () => page.rows,
      };
    },
  });
  document._alpineInit();
  return { store: stores.notifications, fetchCalls };
}

function query(call) {
  return new URLSearchParams(call.url.split('?')[1] || '');
}

function rows(...uuids) {
  return uuids.map((uuid) => ({ uuid, is_read: false }));
}

test('the unread tab, a module tab and the search box each become their filter', async () => {
  const { store, fetchCalls } = loadNotificationsStore([]);

  await store.fetchList({ filter: 'unread', search: 'deploy' });
  await store.fetchList({ filter: 'files', search: '' });
  await store.fetchList({ filter: 'all' });

  assert.equal(query(fetchCalls[0]).get('read'), 'false');
  assert.equal(query(fetchCalls[0]).get('q'), 'deploy');
  assert.equal(query(fetchCalls[1]).get('origin'), 'files');
  assert.deepStrictEqual([...query(fetchCalls[2]).keys()], ['limit']);
});

test('load more skips the rows listed and drops the ones it already holds', async () => {
  const { store, fetchCalls } = loadNotificationsStore([
    { rows: rows('a', 'b'), hasMore: true },
    // A notification arrived since: the second page opens on a row already listed.
    { rows: rows('b', 'c'), hasMore: false },
  ]);

  await store.fetchList();
  await store.loadMore();

  assert.equal(query(fetchCalls[1]).get('offset'), '2');
  assert.deepStrictEqual(store.items.map((n) => n.uuid), ['a', 'b', 'c']);
  assert.equal(store.hasMore, false);
});

test('a refresh reloads as many rows as are listed, within what a page may hold', async () => {
  const { store, fetchCalls } = loadNotificationsStore([]);
  store.items = rows(...Array.from({ length: 1200 }, (_, i) => 'n' + i));

  await store.refresh();

  assert.equal(query(fetchCalls[0]).get('limit'), '1000');
});

test('marking one read sends the field to change', async () => {
  const { store, fetchCalls } = loadNotificationsStore([{ rows: rows('a'), hasMore: false }]);
  await store.fetchList();
  store.unread = 1;

  await store.markRead('a');

  const call = fetchCalls[1];
  assert.equal(call.url, '/api/v1/notifications/a');
  assert.equal(call.opts.method, 'PATCH');
  assert.equal(call.opts.body, JSON.stringify({ is_read: true }));
  assert.equal(store.items[0].is_read, true);
  assert.equal(store.unread, 0);
});

test('marking all read posts to the collection action', async () => {
  const { store, fetchCalls } = loadNotificationsStore([]);
  store.unread = 3;

  await store.markAllRead();

  assert.equal(fetchCalls[0].url, '/api/v1/notifications/mark-all-read');
  assert.equal(fetchCalls[0].opts.method, 'POST');
  assert.equal(store.unread, 0);
});
