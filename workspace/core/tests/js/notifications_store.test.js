'use strict';

// The notification drawer pages with the cursor the server handed back, never
// with a notification it looks up again: that row may have been merged, read
// or deleted since, and the lookup then answered with the first page again.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

function loadNotificationsStore(respond) {
  const stores = {};
  const fetchCalls = [];
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
    documentElement: { lang: 'en' },
  };
  loadScript('workspace/core/static/core/js/stores.js', {
    Alpine,
    document,
    // No service worker: the push store stays idle.
    navigator: {},
    URLSearchParams,
    addEventListener() {},
    getCSRFToken: () => 'csrf',
    fetch: (url) => {
      fetchCalls.push(url);
      return respond(url);
    },
  });
  document._alpineInit();
  return { store: stores.notifications, fetchCalls };
}

function page(titles, nextCursor) {
  return {
    ok: true,
    json: async () => ({
      notifications: titles.map((t) => ({ uuid: t, title: t, is_read: false })),
      has_more: nextCursor !== null,
      next_cursor: nextCursor,
      unread_count: 0,
    }),
  };
}

function cursorOf(url) {
  return new URL(url, 'https://ws.example').searchParams.get('cursor');
}

test('loadMore sends the cursor of the previous response', async () => {
  const { store, fetchCalls } = loadNotificationsStore((url) =>
    Promise.resolve(cursorOf(url) ? page(['c'], null) : page(['a', 'b'], 'tok-1'))
  );
  await store.fetchList();
  // A notification deleted from the drawer must not change where the next page starts.
  store.items = store.items.filter((n) => n.uuid !== 'b');

  await store.loadMore();

  assert.equal(cursorOf(fetchCalls[1]), 'tok-1');
  assert.equal(new URL(fetchCalls[1], 'https://ws.example').searchParams.get('before'), null);
  assert.deepStrictEqual(Array.from(store.items, (n) => n.uuid), ['a', 'c']);
  assert.equal(store.hasMore, false);
});

test('a page requested before the list was refetched is dropped', async () => {
  let releaseMore;
  const { store } = loadNotificationsStore((url) => {
    if (cursorOf(url)) return new Promise((resolve) => { releaseMore = () => resolve(page(['old-next'], null)); });
    return Promise.resolve(page(['a', 'b'], 'tok-1'));
  });
  await store.fetchList();

  const more = store.loadMore();
  await store.fetchList({ filter: 'unread' });
  releaseMore();
  await more;

  assert.deepStrictEqual(Array.from(store.items, (n) => n.uuid), ['a', 'b']);
});
