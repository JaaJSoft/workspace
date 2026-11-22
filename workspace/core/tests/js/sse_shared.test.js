'use strict';

// One stream per browser: the tab holding the Web Lock opens it and relays
// its events to the other tabs of the same user over a BroadcastChannel.
//
// What must hold is what each tab's listeners see - the same window events
// whether the tab holds the stream or not - and that some tab always holds
// it: when the leader closes, when the browser sets its page aside, and when
// it stops answering without having said anything.

const assert = require('node:assert');
const { test } = require('node:test');

const { FakeBrowser, settle } = require('./fake_browser');

const GROUP = 'workspace-sse:7';
const PROBE_INTERVAL_MS = 15000;
const PROBE_TIMEOUT_MS = 3000;

/** A leader and its followers, each one settled before the next opens. */
async function openTabs(browser, count, options) {
  const tabs = [];
  for (let i = 0; i < count; i += 1) {
    tabs.push(browser.openTab(options));
    await settle();
  }
  return tabs;
}

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

test('the tabs of a browser open a single stream', async () => {
  const browser = new FakeBrowser();
  const [leader, follower, third] = await openTabs(browser, 3);

  assert.equal(leader.sources.length, 1);
  assert.equal(leader.stream().url, '/api/v1/stream');
  assert.equal(follower.sources.length, 0);
  assert.equal(third.sources.length, 0);
});

test('an event the leader receives is dispatched in every tab, aliases included', async () => {
  const browser = new FakeBrowser();
  const [leader, follower] = await openTabs(browser, 2);

  leader.stream().deliver({ event: 'chat.unread', data: { total: 3 } }, 'c1');
  leader.stream().deliver({ event: 'ai_stream.bot_step', data: { id: 'x' } });
  await settle();

  const expected = [
    { type: 'sse:chat.unread', detail: { total: 3 } },
    { type: 'chat-unread', detail: { total: 3 } },
    { type: 'sse:ai_stream.bot_step', detail: { id: 'x' } },
    { type: 'ai-stream-bot_step', detail: { id: 'x' } },
  ];
  assert.deepStrictEqual(leader.events, expected);
  assert.deepStrictEqual(follower.events, expected);
});

test('a tab that joins a stream asks the server for the events it opens with', async () => {
  const browser = new FakeBrowser();
  const leader = browser.openTab();
  await settle();
  const follower = browser.openTab({
    snapshot: [{ event: 'notifications.count', data: { unread: 4 } }],
  });
  await settle();

  assert.deepStrictEqual(leader.fetches, [], 'the leader gets them from its stream');
  assert.deepStrictEqual(follower.fetches, ['/api/v1/stream/snapshot']);
  assert.deepStrictEqual(follower.events, [
    { type: 'sse:notifications.count', detail: { unread: 4 } },
  ]);
});

test('relays wait for the snapshot and are applied after it', async () => {
  const browser = new FakeBrowser();
  const leader = browser.openTab();
  await settle();
  const snapshot = deferred();
  const follower = browser.openTab({ snapshot: () => snapshot.promise });
  await settle();

  leader.stream().deliver({ event: 'notifications.count', data: { unread: 5 } });
  await settle();
  assert.deepStrictEqual(follower.events, [], 'a relay must not land ahead of the snapshot');

  snapshot.resolve({
    ok: true,
    json: () => Promise.resolve({ events: [{ event: 'notifications.count', data: { unread: 4 } }] }),
  });
  await settle();

  assert.deepStrictEqual(
    follower.events.map((event) => event.detail.unread),
    [4, 5],
    'the newer value must be the one left standing',
  );
});

test('a snapshot that fails leaves the relays flowing', async () => {
  const browser = new FakeBrowser();
  const leader = browser.openTab();
  await settle();
  const follower = browser.openTab({ snapshot: () => Promise.resolve({ ok: false }) });
  await settle();

  leader.stream().deliver({ event: 'files.operation', data: {} });
  await settle();

  assert.deepStrictEqual(follower.types(), ['sse:files.operation']);
});

test('the first connection stays silent in every tab', async () => {
  const browser = new FakeBrowser();
  const [leader, follower] = await openTabs(browser, 2);

  leader.stream().open();
  await settle();

  assert.deepStrictEqual(leader.types(), []);
  assert.deepStrictEqual(follower.types(), []);
});

test('a stream the leader reopens re-syncs every tab', async () => {
  const browser = new FakeBrowser();
  const [leader, follower] = await openTabs(browser, 2);
  leader.stream().open();

  // EventSource reconnecting on its own, as it does every ten minutes.
  leader.stream().open();
  await settle();

  assert.deepStrictEqual(leader.types(), ['sse:reconnect']);
  assert.deepStrictEqual(follower.types(), ['sse:reconnect']);
});

test('when the leader closes, a follower reopens the stream where the group stopped', async () => {
  const browser = new FakeBrowser();
  const [leader, follower, third] = await openTabs(browser, 3);
  leader.stream().deliver({ event: 'files.operation', data: {} }, 'cursor-7');
  await settle();
  follower.events.length = 0;
  third.events.length = 0;

  browser.close(leader);
  await settle();

  assert.equal(follower.sources.length, 1, 'the next tab in line takes the stream');
  assert.equal(follower.stream().url, '/api/v1/stream?last_event_id=cursor-7');
  assert.equal(third.sources.length, 0);

  // Events without an id are lost between the two streams: every tab re-syncs.
  follower.stream().open();
  await settle();
  assert.deepStrictEqual(follower.types(), ['sse:reconnect']);
  assert.deepStrictEqual(third.types(), ['sse:reconnect']);

  follower.stream().deliver({ event: 'files.operation', data: {} }, 'cursor-8');
  await settle();
  assert.deepStrictEqual(third.types(), ['sse:reconnect', 'sse:files.operation']);
});

test('a follower that saw no event learns the resume point from the leader', async () => {
  const browser = new FakeBrowser();
  const leader = browser.openTab();
  await settle();
  leader.stream().deliver({ event: 'files.operation', data: {} }, 'cursor-7');
  // Opens visible, so it checks on the leader right away.
  const follower = browser.openTab();
  await settle();

  browser.close(leader);
  await settle();

  assert.equal(follower.stream().url, '/api/v1/stream?last_event_id=cursor-7');
});

test('a tab promoted before its snapshot arrived keeps the relays and drops the snapshot', async () => {
  const browser = new FakeBrowser();
  const leader = browser.openTab();
  await settle();
  const snapshot = deferred();
  const follower = browser.openTab({ snapshot: () => snapshot.promise });
  await settle();
  leader.stream().deliver({ event: 'files.operation', data: {} }, 'cursor-7');
  await settle();

  browser.close(leader);
  await settle();
  assert.deepStrictEqual(follower.types(), ['sse:files.operation']);

  // Its own stream opens with fresher events than this answer.
  snapshot.resolve({
    ok: true,
    json: () => Promise.resolve({ events: [{ event: 'notifications.count', data: { unread: 4 } }] }),
  });
  await settle();
  assert.deepStrictEqual(follower.types(), ['sse:files.operation']);
});

test('a visible follower takes over from a leader that stopped answering', async () => {
  const browser = new FakeBrowser();
  const [leader, follower, third] = await openTabs(browser, 3, { hidden: false });
  leader.stream().deliver({ event: 'files.operation', data: {} }, 'cursor-7');
  await settle();
  third.hide();
  third.events.length = 0;

  browser.suspend(leader);
  follower.timers.advance(PROBE_INTERVAL_MS);
  await settle();
  assert.equal(follower.sources.length, 0, 'the leader gets a moment to answer');

  follower.timers.advance(PROBE_TIMEOUT_MS);
  await settle();

  assert.equal(browser.holder(GROUP), follower);
  assert.equal(follower.stream().url, '/api/v1/stream?last_event_id=cursor-7');
  assert.ok(
    !browser.queued(GROUP).includes(follower),
    'its queued request must go, or the lock would be granted to it a second time',
  );

  // The old leader wakes up with events in hand: it still believes it leads
  // until it is told otherwise, and what it relays is no longer the stream.
  leader.stream().deliver({ event: 'chat.typing', data: { stale: true } });
  await settle();
  assert.deepStrictEqual(third.types(), [], 'a replaced leader must not be listened to');
  assert.ok(!follower.types().includes('sse:chat.typing'));

  leader.events.length = 0;
  browser.wake(leader);
  await settle();

  assert.deepStrictEqual(leader.openStreams(), [], 'it closes the stream it lost');
  assert.deepStrictEqual(leader.types(), ['sse:reconnect'], 'and re-syncs, having changed streams');
  assert.deepStrictEqual(leader.fetches, ['/api/v1/stream/snapshot']);
  assert.deepStrictEqual(browser.queued(GROUP), [third, leader], 'back in line for the lock');

  follower.stream().deliver({ event: 'chat.typing', data: { stale: false } });
  await settle();
  assert.deepStrictEqual(leader.events[leader.events.length - 1].detail, { stale: false });
});

test('a leader that answers keeps the stream', async () => {
  const browser = new FakeBrowser();
  const [leader, follower] = await openTabs(browser, 2);

  // An unanswered check would run out during the next round.
  for (let round = 0; round < 3; round += 1) {
    follower.timers.advance(PROBE_INTERVAL_MS);
    await settle();
  }
  follower.timers.advance(PROBE_TIMEOUT_MS);
  await settle();

  assert.equal(browser.holder(GROUP), leader);
  assert.equal(follower.sources.length, 0);
});

test('a leader busy relaying needs no separate answer', async () => {
  const browser = new FakeBrowser();
  const [leader, follower] = await openTabs(browser, 2);

  follower.timers.advance(PROBE_INTERVAL_MS);
  // The leader's answer is held up, but an event gets through.
  browser.suspend(leader);
  await settle();
  browser.post(browser.channels.find((channel) => channel.tab === leader), {
    type: 'event', from: 'someone', id: '', payload: { event: 'chat.typing', data: {} },
  });
  await settle();
  follower.timers.advance(PROBE_TIMEOUT_MS);
  await settle();

  assert.equal(follower.sources.length, 0, 'any relay proves the leader is alive');
});

test('a hidden follower leaves the leader alone until someone looks at it', async () => {
  const browser = new FakeBrowser();
  const leader = browser.openTab();
  await settle();
  const follower = browser.openTab({ hidden: true });
  await settle();

  browser.suspend(leader);
  follower.timers.advance(4 * PROBE_INTERVAL_MS);
  await settle();
  assert.equal(follower.sources.length, 0, 'nobody is looking at this tab');

  follower.show();
  await settle();
  follower.timers.advance(PROBE_TIMEOUT_MS);
  await settle();
  assert.equal(follower.sources.length, 1, 'shown, it checks at once');
});

for (const [leaving, returning] of [['pagehide', 'pageshow'], ['freeze', 'resume']]) {
  test(`a page set aside (${leaving}) hands the stream over and joins again on ${returning}`, async () => {
    const browser = new FakeBrowser();
    const [leader, follower] = await openTabs(browser, 2);
    leader.stream().deliver({ event: 'files.operation', data: {} }, 'cursor-7');
    await settle();
    const handlers = leaving === 'pagehide' ? leader.windowHandlers : leader.documentHandlers;

    handlers[leaving]({ persisted: true });
    await settle();

    assert.deepStrictEqual(leader.openStreams(), []);
    assert.equal(browser.holder(GROUP), follower);
    assert.equal(follower.stream().url, '/api/v1/stream?last_event_id=cursor-7');

    // Nothing reaches a page that is set aside.
    leader.events.length = 0;
    follower.stream().deliver({ event: 'files.operation', data: {} }, 'cursor-8');
    await settle();
    assert.deepStrictEqual(leader.types(), []);

    handlers[returning]({ persisted: true });
    await settle();

    assert.deepStrictEqual(leader.openStreams(), [], 'the stream stays with the tab that took it');
    assert.deepStrictEqual(leader.types(), ['sse:reconnect'], 'it missed everything meanwhile');
    assert.deepStrictEqual(leader.fetches, ['/api/v1/stream/snapshot']);
    assert.deepStrictEqual(browser.queued(GROUP), [leader]);
  });
}

test('a bfcache restore that fires both resume and pageshow joins once', async () => {
  const browser = new FakeBrowser();
  const [leader, follower] = await openTabs(browser, 2);

  follower.windowHandlers.pagehide({ persisted: true });
  follower.documentHandlers.freeze();
  await settle();
  assert.deepStrictEqual(browser.queued(GROUP), []);

  follower.documentHandlers.resume();
  follower.windowHandlers.pageshow({ persisted: true });
  await settle();

  assert.deepStrictEqual(browser.queued(GROUP), [follower]);
  assert.equal(follower.fetches.length, 2, 'one snapshot per stay');
  assert.equal(browser.holder(GROUP), leader);
});

test('the only tab leads again after a bfcache restore and re-syncs', async () => {
  const browser = new FakeBrowser();
  const tab = browser.openTab();
  await settle();
  tab.stream().open();
  tab.stream().deliver({ event: 'files.operation', data: {} }, 'cursor-7');

  tab.windowHandlers.pagehide({ persisted: true });
  await settle();
  assert.equal(browser.holder(GROUP), null, 'a page in the bfcache must not keep the lock');

  tab.windowHandlers.pageshow({ persisted: true });
  await settle();

  assert.equal(tab.sources.length, 2);
  assert.equal(tab.stream().url, '/api/v1/stream?last_event_id=cursor-7');
  tab.events.length = 0;
  tab.stream().open();
  assert.deepStrictEqual(tab.types(), ['sse:reconnect']);
});

test('a leader whose stream closed reopens it when its tab is shown again', async () => {
  const browser = new FakeBrowser();
  const [leader, follower] = await openTabs(browser, 2);

  leader.stream().close();
  leader.hide();
  leader.show();
  await settle();

  assert.equal(leader.sources.length, 2);
  assert.equal(follower.sources.length, 0);
});

test('a tab that took the stream while its own request was being granted ends up leading once', async () => {
  const browser = new FakeBrowser();
  const [leader, follower] = await openTabs(browser, 2);

  // The leader goes at the very moment the follower gives up on it: the
  // follower's queued request is granted, and its steal lands on top.
  browser.suspend(leader);
  follower.timers.advance(PROBE_INTERVAL_MS);
  await settle();
  browser.close(leader);
  follower.timers.advance(PROBE_TIMEOUT_MS);
  await settle();

  assert.equal(browser.holder(GROUP), follower);
  assert.equal(follower.openStreams().length, 1, 'losing its first hold must not close the stream');
  assert.deepStrictEqual(browser.queued(GROUP), []);

  const third = browser.openTab();
  await settle();
  follower.windowHandlers.pagehide({ persisted: false });
  await settle();
  assert.equal(browser.holder(GROUP), third, 'leaving lets go of whichever hold is the live one');
});

test('without Web Locks every tab keeps a stream of its own', async () => {
  const browser = new FakeBrowser();
  const [first, second] = await openTabs(browser, 2, { locks: null });

  assert.equal(first.sources.length, 1);
  assert.equal(second.sources.length, 1);
  assert.deepStrictEqual(second.fetches, []);
  assert.deepStrictEqual(browser.channels, []);

  first.stream().deliver({ event: 'files.operation', data: {} });
  await settle();
  assert.deepStrictEqual(second.types(), []);
});

test('a page that names no user keeps a stream of its own', async () => {
  const browser = new FakeBrowser();
  const [first, second] = await openTabs(browser, 2, { userId: null });

  assert.equal(first.sources.length, 1);
  assert.equal(second.sources.length, 1);
});

test('tabs of two accounts share nothing', async () => {
  const browser = new FakeBrowser();
  const mine = browser.openTab({ userId: '7' });
  await settle();
  const theirs = browser.openTab({ userId: '8' });
  await settle();

  assert.equal(mine.sources.length, 1);
  assert.equal(theirs.sources.length, 1);

  mine.stream().deliver({ event: 'notifications.count', data: { unread: 4 } });
  await settle();
  assert.deepStrictEqual(theirs.types(), []);
});

test('a lock request the browser refuses falls back to a stream of its own', async () => {
  const browser = new FakeBrowser();
  const refusing = {
    request: () => Promise.reject(new DOMException('refused', 'SecurityError')),
  };
  const tab = browser.openTab({ locks: refusing });
  await settle();

  assert.equal(tab.sources.length, 1);
  assert.equal(tab.stream().url, '/api/v1/stream');
  tab.stream().open();
  assert.deepStrictEqual(tab.types(), [], 'still a first connection');

  // And from there on it behaves as a browser without Web Locks does.
  tab.windowHandlers.pageshow({ persisted: true });
  assert.equal(tab.sources.length, 2);
});
