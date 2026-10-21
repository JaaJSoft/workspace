'use strict';

// Regression tests for the Alpine `presence` store in stores.js.
//
// The store drives the presence ring/dot around every user avatar. Bots are
// static identity (they have no UserPresence row, so they only ever appear in
// the `bot` bucket): a presence update that does not carry a `bot` array must
// NOT erase the previously known bots, otherwise every bot ring flickers to
// "offline" until the next full snapshot. These tests pin that behaviour.

const assert = require('node:assert');
const { test } = require('node:test');
const { loadScript } = require('../../../common/tests/js/loader');

// stores.js registers everything inside an `alpine:init` listener and reads
// `navigator`/`document` at load. Build minimal stubs that capture the
// registered stores so we can exercise the presence store directly.
function loadPresenceStore() {
  return loadPresenceEnv().store;
}

// Same, keeping hold of what the store sends: the watch requests it posts,
// the timer that batches them, and the window listeners it registers.
function loadPresenceEnv() {
  const stores = {};
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
  const env = { posts: [], timers: [], listeners: {}, responses: [] };
  env.dispatch = function (type, event) {
    (env.listeners[type] || []).forEach((cb) => cb(event || {}));
  };
  env.flush = function () {
    const timers = env.timers.splice(0);
    timers.forEach((fn) => fn());
  };
  loadScript('workspace/core/static/core/js/stores.js', {
    Alpine,
    document,
    navigator: {},
    addEventListener(type, cb) {
      (env.listeners[type] = env.listeners[type] || []).push(cb);
    },
    setTimeout(fn) {
      env.timers.push(fn);
      return env.timers.length;
    },
    fetch(url, opts) {
      env.posts.push({ url, body: JSON.parse(opts.body) });
      // A queued response is a status map, a promise of one (to hold the
      // answer back), or an Error for a failed request.
      const next = env.responses.shift();
      if (next instanceof Error) return Promise.resolve({ ok: false, status: 500 });
      return Promise.resolve(next).then((statuses) => ({
        ok: true,
        json: () => Promise.resolve(statuses || {}),
      }));
    },
    getCSRFToken: () => 'csrf',
  });
  // Fire alpine:init so the stores get registered on our Alpine stub.
  document._alpineInit();
  env.store = stores.presence;
  return env;
}

test('handleSnapshot buckets users and bots from a full snapshot', () => {
  const p = loadPresenceStore();
  p.handleSnapshot({ online: [1], away: [2], busy: [3], bot: [42] });

  assert.equal(p.statusOf(1), 'online');
  assert.equal(p.statusOf(2), 'away');
  assert.equal(p.statusOf(3), 'busy');
  assert.equal(p.statusOf(42), 'bot');
});

test('a snapshot that omits `bot` keeps the previously known bots', () => {
  const p = loadPresenceStore();
  p.handleSnapshot({ online: [1], away: [], busy: [], bot: [42] });
  assert.equal(p.statusOf(42), 'bot');

  // A later presence update carries no bot info (e.g. partial/empty payload).
  // The bot ring must survive — this is the regression: with a destructive
  // full-replace (`new Set(data.bot || [])`) the bot would drop to 'offline'.
  p.handleSnapshot({ online: [1], away: [], busy: [] });
  assert.equal(p.statusOf(42), 'bot');
});

test('a null/undefined payload does not clear existing presence', () => {
  const p = loadPresenceStore();
  p.handleSnapshot({ online: [1], away: [], busy: [], bot: [42] });

  p.handleSnapshot(undefined);
  p.handleSnapshot(null);

  assert.equal(p.statusOf(1), 'online');
  assert.equal(p.statusOf(42), 'bot');
});

test('an explicit empty `bot` array still clears bots (real deletion)', () => {
  const p = loadPresenceStore();
  p.handleSnapshot({ online: [], away: [], busy: [], bot: [42] });
  assert.equal(p.statusOf(42), 'bot');

  // When the server explicitly reports zero bots, honour it.
  p.handleSnapshot({ online: [], away: [], busy: [], bot: [] });
  assert.equal(p.statusOf(42), 'offline');
});

test('setLocalStatus moves the user into the chosen bucket', () => {
  const p = loadPresenceStore();
  p.handleSnapshot({ online: [], away: [], busy: [], bot: [] });

  p.setLocalStatus(7, 'busy');
  assert.equal(p.statusOf(7), 'busy');

  p.setLocalStatus(7, 'away');
  assert.equal(p.statusOf(7), 'away');

  p.setLocalStatus(7, 'online');
  assert.equal(p.statusOf(7), 'online');

  // Invisible (or any unknown) leaves the user out of every bucket.
  p.setLocalStatus(7, 'invisible');
  assert.equal(p.statusOf(7), 'offline');
});

test('setLocalStatus reassigns the Sets so Alpine reactivity fires', () => {
  const p = loadPresenceStore();
  p.handleSnapshot({ online: [], away: [], busy: [], bot: [] });

  const beforeOnline = p.online;
  const beforeBusy = p.busy;
  p.setLocalStatus(7, 'busy');

  // The buggy navbar mutated the Sets in place (p.online.add/delete), which
  // Alpine does not track. The fix must hand back fresh Set instances.
  assert.notStrictEqual(p.online, beforeOnline);
  assert.notStrictEqual(p.busy, beforeBusy);
});

test('setLocalStatus does not disturb other users or bots', () => {
  const p = loadPresenceStore();
  p.handleSnapshot({ online: [1], away: [], busy: [], bot: [42] });

  p.setLocalStatus(7, 'away');
  assert.equal(p.statusOf(1), 'online');
  assert.equal(p.statusOf(42), 'bot');
  assert.equal(p.statusOf(7), 'away');
});

test('volatile buckets still update to empty (users genuinely go offline)', () => {
  const p = loadPresenceStore();
  p.handleSnapshot({ online: [1], away: [], busy: [], bot: [] });
  assert.equal(p.statusOf(1), 'online');

  p.handleSnapshot({ online: [], away: [], busy: [], bot: [] });
  assert.equal(p.statusOf(1), 'offline');
});

test('handleChanges moves only the listed users', () => {
  const p = loadPresenceStore();
  p.handleSnapshot({ online: [1, 2], away: [3], busy: [], bot: [42] });

  p.handleChanges({ 1: 'busy', 3: 'offline', 9: 'online' });

  assert.equal(p.statusOf(1), 'busy');
  assert.equal(p.statusOf(2), 'online');
  assert.equal(p.statusOf(3), 'offline');
  assert.equal(p.statusOf(9), 'online');
  assert.equal(p.statusOf(42), 'bot');
});

test('handleChanges reassigns the Sets so Alpine reactivity fires', () => {
  const p = loadPresenceStore();
  p.handleSnapshot({ online: [1], away: [], busy: [], bot: [] });
  const before = p.online;

  p.handleChanges({ 1: 'away' });

  assert.notStrictEqual(p.online, before);
});

test('reading a status asks the server for it once, in one batch', async () => {
  const env = loadPresenceEnv();
  const p = env.store;

  p.statusOf(5);
  p.ringClass(6);
  p.dotClass('5');
  assert.equal(env.posts.length, 0, 'reads are batched, not posted one by one');

  env.responses.push({ 5: 'online', 6: 'offline' });
  env.flush();
  assert.equal(env.posts.length, 1);
  assert.equal(env.posts[0].url, '/api/v1/users/presence');
  assert.deepStrictEqual(Array.from(env.posts[0].body.user_ids), [5, 6]);

  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(p.statusOf(5), 'online');

  // Already asked: no second request.
  p.statusOf(5);
  env.flush();
  assert.equal(env.posts.length, 1);
});

test('a stream reconnect sends every watched id again', () => {
  const env = loadPresenceEnv();
  env.store.statusOf(5);
  env.store.statusOf(6);
  env.flush();

  env.dispatch('sse:reconnect');

  assert.equal(env.posts.length, 2);
  assert.deepStrictEqual(Array.from(env.posts[1].body.user_ids), [5, 6]);
});

test('a presence_changes event applies the changes', () => {
  const env = loadPresenceEnv();
  env.store.handleSnapshot({ online: [1], away: [], busy: [], bot: [] });

  env.dispatch('sse:presence.presence_changes', { detail: { 1: 'away' } });

  assert.equal(env.store.statusOf(1), 'away');
});

const settle = () => new Promise((resolve) => setImmediate(resolve));

test('an answer does not override a stream change received after the request', async () => {
  const env = loadPresenceEnv();
  let answer;
  env.responses.push(new Promise((resolve) => { answer = resolve; }));
  env.store.statusOf(5);
  env.flush();

  env.dispatch('sse:presence.presence_changes', { detail: { 5: 'away' } });
  answer({ 5: 'online', 6: 'busy' });
  await settle();

  assert.equal(env.store.statusOf(5), 'away', 'the newer stream change wins');
  assert.equal(env.store.statusOf(6), 'busy', 'untouched ids still apply');
});

test('a failed request is asked again on the next status read', async () => {
  const env = loadPresenceEnv();
  env.responses.push(new Error('server error'));
  env.store.statusOf(5);
  env.flush();
  await settle();

  env.responses.push({ 5: 'online' });
  env.store.statusOf(5);
  env.flush();
  await settle();

  assert.equal(env.posts.length, 2);
  assert.deepStrictEqual(Array.from(env.posts[1].body.user_ids), [5]);
  assert.equal(env.store.statusOf(5), 'online');
});
