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
  loadScript('workspace/common/static/ui/js/stores.js', {
    Alpine,
    document,
    navigator: {},
    addEventListener() {},
    getCSRFToken: () => 'csrf',
  });
  // Fire alpine:init so the stores get registered on our Alpine stub.
  document._alpineInit();
  return stores.presence;
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
