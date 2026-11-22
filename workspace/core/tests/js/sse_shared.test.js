'use strict';

// One stream per browser: a shared worker opens it and posts its events to
// every tab of the same user.
//
// What must hold is what each tab's listeners see - the same window events as
// when the tab held the stream itself - that a tab going away costs the
// others nothing, and that a tab is never left without events: when the
// stream fails, when the worker dies, and when there is no worker to be had.

const assert = require('node:assert');
const { test } = require('node:test');

const { FakeBrowser, SCRIPT_URL, settle } = require('./fake_browser');

const RETRY_MS = 30000;
const ANSWER_TIMEOUT_MS = 5000;
const CHECK_INTERVAL_MS = 20000;
const SNAPSHOT_TIMEOUT_MS = 5000;

/** Tabs opened one after the other, each one settled before the next. */
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

function answering(events) {
  return { ok: true, json: () => Promise.resolve({ events }) };
}

const COUNT_4 = { event: 'notifications.count', data: { unread: 4 } };
const COUNT_5 = { event: 'notifications.count', data: { unread: 5 } };
const OPERATION = { event: 'files.operation', data: {} };

// -- one stream, for every tab ----------------------------------------------

test('the tabs of a browser open a single stream, in a worker', async () => {
  const browser = new FakeBrowser();
  const tabs = await openTabs(browser, 3);

  const worker = browser.worker();
  assert.equal(worker.sources.length, 1);
  assert.equal(worker.stream().url, '/api/v1/stream');
  for (const tab of tabs) assert.equal(tab.sources.length, 0);
});

test('the worker runs the file the page loaded, under the name of its user', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);

  assert.equal(tab.sharedWorkers.length, 1);
  assert.equal(tab.sharedWorkers[0].url, SCRIPT_URL);
  assert.equal(tab.sharedWorkers[0].name, 'workspace-sse:7');
});

test('an event is dispatched in every tab, aliases included', async () => {
  const browser = new FakeBrowser();
  const [first, second] = await openTabs(browser, 2);

  browser.worker().stream().deliver({ event: 'chat.unread', data: { total: 3 } }, 'c1');
  browser.worker().stream().deliver({ event: 'ai_stream.bot_step', data: { id: 'x' } });
  await settle();

  const expected = [
    { type: 'sse:chat.unread', detail: { total: 3 } },
    { type: 'chat-unread', detail: { total: 3 } },
    { type: 'sse:ai_stream.bot_step', detail: { id: 'x' } },
    { type: 'ai-stream-bot_step', detail: { id: 'x' } },
  ];
  assert.deepStrictEqual(first.events, expected);
  assert.deepStrictEqual(second.events, expected);
});

test('tabs of two accounts share nothing', async () => {
  const browser = new FakeBrowser();
  const mine = browser.openTab({ userId: '7' });
  const theirs = browser.openTab({ userId: '8' });
  await settle();

  assert.equal(browser.workers.size, 2);
  const [myWorker, theirWorker] = [mine, theirs].map(
    (tab) => [...browser.workers.values()].find((worker) => worker.tabs.has(tab)),
  );
  assert.notEqual(myWorker, theirWorker);

  myWorker.stream().deliver(COUNT_4);
  await settle();
  assert.deepStrictEqual(mine.types(), ['sse:notifications.count']);
  assert.deepStrictEqual(theirs.types(), []);
});

// -- a tab that joins a stream already open ---------------------------------

test('the tab that starts the stream reads no snapshot, the next ones do', async () => {
  const browser = new FakeBrowser();
  const first = browser.openTab();
  await settle();
  const second = browser.openTab({ snapshot: [COUNT_4] });
  await settle();

  assert.deepStrictEqual(first.fetches, [], 'its stream is about to send them');
  assert.deepStrictEqual(second.fetches, ['/api/v1/stream/snapshot']);
  assert.deepStrictEqual(second.events, [
    { type: 'sse:notifications.count', detail: { unread: 4 } },
  ]);
});

test('events wait for the snapshot and are applied after it', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const snapshot = deferred();
  const tab = browser.openTab({ snapshot: () => snapshot.promise });
  await settle();

  browser.worker().stream().deliver(COUNT_5);
  await settle();
  assert.deepStrictEqual(tab.events, [], 'an event must not land ahead of the snapshot');

  snapshot.resolve(answering([COUNT_4]));
  await settle();

  assert.deepStrictEqual(
    tab.events.map((event) => event.detail.unread),
    [4, 5],
    'the newer value must be the one left standing',
  );
});

test('a reopening announced while the snapshot loads is not replayed to a page that has just rendered', async () => {
  const browser = new FakeBrowser();
  const [first] = await openTabs(browser, 1);
  const stream = browser.worker().stream();
  stream.open();
  const snapshot = deferred();
  const tab = browser.openTab({ snapshot: () => snapshot.promise });
  await settle();

  // The stream reconnects on its own, and sends its first events again.
  stream.open();
  stream.deliver(COUNT_5);
  await settle();
  snapshot.resolve(answering([COUNT_4]));
  await settle();

  assert.deepStrictEqual(first.types(), ['sse:reconnect', 'sse:notifications.count']);
  assert.deepStrictEqual(tab.types(), ['sse:notifications.count', 'sse:notifications.count']);
});

test('a refused snapshot leaves the events flowing', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const snapshot = deferred();
  const tab = browser.openTab({ snapshot: () => snapshot.promise });
  await settle();
  browser.worker().stream().deliver(OPERATION);
  await settle();

  // An error page can carry a body: it is not a snapshot.
  snapshot.resolve({ ok: false, json: () => Promise.resolve({ events: [COUNT_4] }) });
  await settle();

  assert.deepStrictEqual(tab.types(), ['sse:files.operation']);
});

test('an answer that is no snapshot leaves the events flowing', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const snapshot = deferred();
  const tab = browser.openTab({ snapshot: () => snapshot.promise });
  await settle();
  browser.worker().stream().deliver(OPERATION);
  await settle();

  snapshot.resolve({ ok: true, json: () => Promise.resolve({}) });
  await settle();

  assert.deepStrictEqual(tab.types(), ['sse:files.operation']);
});

test('a snapshot that never answers stops holding the events back', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const stalled = (url, init) => new Promise((resolve, reject) => {
    init.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
  });
  const tab = browser.openTab({ snapshot: stalled });
  await settle();

  browser.worker().stream().deliver(OPERATION);
  await settle();
  assert.deepStrictEqual(tab.types(), []);

  tab.timers.advance(SNAPSHOT_TIMEOUT_MS);
  await settle();
  assert.deepStrictEqual(tab.types(), ['sse:files.operation']);
});

test('a snapshot answered after the page was set aside is dropped', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const snapshot = deferred();
  const tab = browser.openTab({ snapshot: () => snapshot.promise });
  await settle();

  tab.windowHandlers.pagehide({ persisted: true });
  snapshot.resolve(answering([COUNT_4]));
  await settle();

  assert.deepStrictEqual(tab.types(), []);
});

test('a tab that saw no event learns from the worker where the stream stands', async () => {
  const browser = new FakeBrowser();
  const [first] = await openTabs(browser, 1);
  const worker = browser.worker();
  worker.stream().deliver(OPERATION, 'cursor-7');
  const [second] = await openTabs(browser, 1);
  browser.close(first);

  // Left to start the stream again, it resumes it there.
  browser.kill(worker);
  second.timers.advance(CHECK_INTERVAL_MS);
  await settle();
  second.timers.advance(ANSWER_TIMEOUT_MS);
  await settle();

  assert.equal(browser.worker().stream().url, '/api/v1/stream?last_event_id=cursor-7');
});

// -- the stream opening, and opening again ----------------------------------

test('the first opening stays silent in every tab', async () => {
  const browser = new FakeBrowser();
  const [first, second] = await openTabs(browser, 2);

  browser.worker().stream().open();
  await settle();

  assert.deepStrictEqual(first.types(), []);
  assert.deepStrictEqual(second.types(), []);
});

test('a stream that opens again re-syncs every tab', async () => {
  const browser = new FakeBrowser();
  const [first, second] = await openTabs(browser, 2);
  const stream = browser.worker().stream();
  stream.open();

  // EventSource reconnecting on its own, as it does every ten minutes.
  stream.open();
  await settle();

  assert.deepStrictEqual(first.types(), ['sse:reconnect']);
  assert.deepStrictEqual(second.types(), ['sse:reconnect']);
});

test('a stream the browser gave up on is opened again where it stopped', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);
  const worker = browser.worker();
  worker.stream().open();
  worker.stream().deliver(OPERATION, 'cursor-7');
  await settle();
  tab.events.length = 0;

  worker.stream().fail();
  assert.equal(worker.sources.length, 1, 'not at once: the server is likely restarting');

  worker.timers.advance(RETRY_MS);
  assert.equal(worker.sources.length, 2);
  assert.equal(worker.stream().url, '/api/v1/stream?last_event_id=cursor-7');

  worker.stream().open();
  await settle();
  assert.deepStrictEqual(tab.types(), ['sse:reconnect']);
});

test('a stream that keeps losing its connection is given a rest, then opened again', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const worker = browser.worker();
  const stream = worker.stream();

  for (let i = 0; i < 5; i += 1) stream.stumble();
  assert.equal(stream.closed, false, 'the browser is left to reconnect on its own');

  stream.stumble();
  assert.equal(stream.closed, true);
  worker.timers.advance(RETRY_MS);
  assert.equal(worker.sources.length, 2);
});

test('an opening that follows a failure is announced, the first one included', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);
  const worker = browser.worker();

  // The page has been waiting for its stream since it rendered.
  worker.stream().fail();
  worker.timers.advance(RETRY_MS);
  worker.stream().open();
  await settle();

  assert.deepStrictEqual(tab.types(), ['sse:reconnect']);
});

test('a retry is dropped with the stream it was for', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);
  const worker = browser.worker();
  worker.stream().fail();

  // The last tab listening goes before the retry is due.
  tab.windowHandlers.pagehide({ persisted: true });
  await settle();
  worker.timers.advance(RETRY_MS);

  assert.equal(worker.sources.length, 1);
});

test('a page someone looks at has a closed stream opened again at once', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);
  const worker = browser.worker();
  worker.stream().fail();

  tab.hide();
  tab.show();
  await settle();
  assert.equal(worker.sources.length, 2, 'shown again');

  worker.stream().fail();
  browser.openTab();
  await settle();
  assert.equal(worker.sources.length, 3, 'a page opened next to it');
});

test('a hidden page opening leaves a closed stream to its retry', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const worker = browser.worker();
  worker.stream().fail();

  browser.openTab({ hidden: true });
  await settle();

  assert.equal(worker.sources.length, 1);
});

test('the periodic check leaves a closed stream to its retry', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);
  const worker = browser.worker();
  worker.stream().fail();

  tab.timers.advance(CHECK_INTERVAL_MS);
  await settle();

  assert.equal(worker.sources.length, 1);
});

test('a stream that is open is left alone when a page is shown', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);

  tab.hide();
  tab.show();
  await settle();

  assert.equal(browser.worker().sources.length, 1);
});

// -- tabs coming and going ----------------------------------------------------

test('a tab that goes takes nothing with it', async () => {
  const browser = new FakeBrowser();
  const [first, second, third] = await openTabs(browser, 3);
  const worker = browser.worker();
  worker.stream().open();

  // The tab that started the worker, closed without a word.
  browser.close(first);
  await settle();

  assert.equal(browser.worker(), worker);
  assert.equal(worker.sources.length, 1);
  assert.equal(worker.stream().closed, false);

  worker.stream().deliver(OPERATION);
  await settle();
  assert.deepStrictEqual(second.types(), ['sse:files.operation'], 'no re-sync either');
  assert.deepStrictEqual(third.types(), ['sse:files.operation']);
});

test('a tab that navigates leaves the stream alone and joins it again', async () => {
  const browser = new FakeBrowser();
  const [navigating, staying] = await openTabs(browser, 2);
  const worker = browser.worker();

  navigating.windowHandlers.pagehide({ persisted: false });
  browser.close(navigating);
  const landed = browser.openTab({ snapshot: [COUNT_4] });
  await settle();

  assert.equal(worker.sources.length, 1);
  assert.equal(worker.stream().closed, false);
  assert.deepStrictEqual(staying.types(), []);
  assert.deepStrictEqual(landed.fetches, ['/api/v1/stream/snapshot']);
  assert.deepStrictEqual(landed.types(), ['sse:notifications.count']);
});

test('the stream goes with the last tab listening, and comes back with the next', async () => {
  const browser = new FakeBrowser();
  const [first, second] = await openTabs(browser, 2);
  const worker = browser.worker();

  first.windowHandlers.pagehide({ persisted: true });
  await settle();
  assert.equal(worker.stream().closed, false, 'someone still listens');

  second.windowHandlers.pagehide({ persisted: true });
  await settle();
  assert.equal(worker.stream().closed, true, 'pages in the bfcache keep the worker, not a stream');

  const next = browser.openTab();
  await settle();
  assert.equal(worker.sources.length, 2);
  assert.equal(worker.stream().closed, false);
  assert.deepStrictEqual(next.fetches, [], 'it started this stream');
});

test('a page set aside is sent nothing', async () => {
  const browser = new FakeBrowser();
  const [staying, aside] = await openTabs(browser, 2);

  aside.windowHandlers.pagehide({ persisted: true });
  await settle();
  browser.worker().stream().deliver(OPERATION);
  await settle();

  assert.deepStrictEqual(staying.types(), ['sse:files.operation']);
  assert.deepStrictEqual(aside.types(), []);
});

for (const [leaving, returning] of [['pagehide', 'pageshow'], ['freeze', 'resume']]) {
  test(`a page back from ${leaving} re-syncs and asks what was queued since it left`, async () => {
    const browser = new FakeBrowser();
    const [staying, away] = await openTabs(browser, 2);
    const worker = browser.worker();
    worker.stream().deliver(OPERATION, 'cursor-7');
    await settle();
    away.events.length = 0;
    const handlers = leaving === 'pagehide' ? away.windowHandlers : away.documentHandlers;

    handlers[leaving]({ persisted: true });
    await settle();
    worker.stream().deliver(OPERATION, 'cursor-8');
    await settle();

    handlers[returning]({ persisted: true });
    await settle();

    assert.deepStrictEqual(away.types(), ['sse:reconnect']);
    assert.deepStrictEqual(away.fetches, [
      '/api/v1/stream/snapshot',
      '/api/v1/stream/snapshot?last_event_id=cursor-7',
    ]);
    assert.equal(worker.sources.length, 1, 'the stream never moved');
    assert.deepStrictEqual(staying.types(), ['sse:files.operation', 'sse:files.operation']);
  });
}

test('the only page, restored from the bfcache, starts the stream again where it stopped', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);
  const worker = browser.worker();
  worker.stream().open();
  worker.stream().deliver(OPERATION, 'cursor-7');
  await settle();
  tab.events.length = 0;

  tab.windowHandlers.pagehide({ persisted: true });
  await settle();
  tab.windowHandlers.pageshow({ persisted: true });
  await settle();

  assert.equal(worker.sources.length, 2);
  assert.equal(worker.stream().url, '/api/v1/stream?last_event_id=cursor-7');
  assert.deepStrictEqual(tab.fetches, [], 'the stream it started sends its first events itself');

  worker.stream().open();
  await settle();
  assert.deepStrictEqual(tab.types(), ['sse:reconnect'], 'told once, when it came back');
});

test('a restore that fires both resume and pageshow joins once', async () => {
  const browser = new FakeBrowser();
  const [, tab] = await openTabs(browser, 2);

  tab.windowHandlers.pagehide({ persisted: true });
  tab.documentHandlers.freeze();
  await settle();
  tab.documentHandlers.resume();
  tab.windowHandlers.pageshow({ persisted: true });
  await settle();

  assert.equal(tab.sharedWorkers.length, 2, 'once at load, once on the way back');
  assert.deepStrictEqual(tab.types(), ['sse:reconnect']);

  tab.events.length = 0;
  browser.worker().stream().deliver(OPERATION);
  await settle();
  assert.deepStrictEqual(tab.types(), ['sse:files.operation'], 'a second port would hear it twice');
});

test('a normal load fires pageshow without persisted and joins nothing again', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);

  tab.windowHandlers.pageshow({ persisted: false });
  await settle();

  assert.equal(tab.sharedWorkers.length, 1);
  assert.deepStrictEqual(tab.types(), []);
});

// -- a worker that is not there -----------------------------------------------

test('a worker the browser ended is replaced by the page that notices', async () => {
  const browser = new FakeBrowser();
  const [tab, other] = await openTabs(browser, 2);
  const worker = browser.worker();
  worker.stream().deliver(OPERATION, 'cursor-7');
  await settle();
  tab.events.length = 0;
  other.hide();

  browser.kill(worker);
  tab.timers.advance(CHECK_INTERVAL_MS);
  await settle();
  assert.equal(tab.sharedWorkers.length, 1, 'the worker gets a moment to answer');

  tab.timers.advance(ANSWER_TIMEOUT_MS);
  await settle();

  const replacement = browser.worker();
  assert.notEqual(replacement, worker);
  assert.equal(replacement.stream().url, '/api/v1/stream?last_event_id=cursor-7');
  assert.deepStrictEqual(tab.types(), ['sse:reconnect'], 'it changed streams');

  replacement.stream().deliver(OPERATION, 'cursor-8');
  await settle();
  assert.deepStrictEqual(tab.types(), ['sse:reconnect', 'sse:files.operation']);

  // The other one finds out when someone looks at it, and joins the same worker.
  other.events.length = 0;
  other.show();
  await settle();
  other.timers.advance(ANSWER_TIMEOUT_MS);
  await settle();
  assert.equal(browser.worker(), replacement);
  assert.equal(replacement.sources.length, 1);
  assert.deepStrictEqual(other.types(), ['sse:reconnect']);
  assert.equal(other.fetches[other.fetches.length - 1], '/api/v1/stream/snapshot?last_event_id=cursor-7');
});

test('a page shown before the worker answered does not give up on it', async () => {
  const browser = new FakeBrowser();
  const tab = browser.openTab();
  // Two waits for an answer at once: the first must not outlive the answer.
  tab.show();
  await settle();

  tab.timers.advance(ANSWER_TIMEOUT_MS);
  await settle();

  assert.equal(tab.sources.length, 0);
  assert.equal(tab.sharedWorkers.length, 1);
});

test('a worker that answers is left alone', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);

  // An unanswered check would run out during the next round.
  for (let round = 0; round < 3; round += 1) {
    tab.timers.advance(CHECK_INTERVAL_MS);
    await settle();
  }
  tab.timers.advance(ANSWER_TIMEOUT_MS);
  await settle();

  assert.equal(tab.sharedWorkers.length, 1);
  assert.deepStrictEqual(tab.types(), []);
  assert.deepStrictEqual(tab.fetches, [], 'an answer to a check is no invitation to a snapshot');
});

test('a worker busy sending events needs no separate answer', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);
  const worker = browser.worker();

  // The check never gets its answer, but an event comes through.
  worker.deaf = true;
  tab.timers.advance(CHECK_INTERVAL_MS);
  await settle();
  worker.deaf = false;
  worker.stream().deliver(OPERATION);
  await settle();
  tab.timers.advance(ANSWER_TIMEOUT_MS);
  await settle();

  assert.equal(tab.sharedWorkers.length, 1, 'any message proves the worker is there');
});

test('a hidden page does not check on the worker until someone looks at it', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1, { hidden: true });
  browser.openTab();
  await settle();
  const worker = browser.worker();

  browser.kill(worker);
  tab.timers.advance(4 * CHECK_INTERVAL_MS);
  await settle();
  assert.equal(tab.sharedWorkers.length, 1, 'nobody is looking at this tab');

  tab.show();
  await settle();
  tab.timers.advance(ANSWER_TIMEOUT_MS);
  await settle();
  assert.equal(tab.sharedWorkers.length, 2, 'shown, it checks at once');
});

test('a worker that never starts leaves the page a stream of its own', async () => {
  const browser = new FakeBrowser();
  browser.workersStart = false;
  const [tab] = await openTabs(browser, 1);
  assert.equal(tab.sources.length, 0);

  tab.timers.advance(ANSWER_TIMEOUT_MS);
  await settle();

  assert.equal(tab.sources.length, 1);
  assert.equal(tab.stream().url, '/api/v1/stream');
  tab.stream().open();
  assert.deepStrictEqual(tab.types(), [], 'still a first opening');

  // From there on it behaves as a browser without shared workers does.
  tab.windowHandlers.pagehide({ persisted: true });
  assert.equal(tab.stream().closed, false);
  tab.documentHandlers.resume();
  tab.windowHandlers.pageshow({ persisted: true });
  assert.equal(tab.sources.length, 2);
  assert.equal(tab.sharedWorkers.length, 1);
});

test('a shared worker the browser refuses leaves the page a stream of its own', async () => {
  const browser = new FakeBrowser();
  // As under a content security policy that allows no worker.
  const [tab] = await openTabs(browser, 1, { sharedWorker: 'refused' });

  assert.equal(browser.workers.size, 0);
  assert.equal(tab.sources.length, 1);
  tab.stream().open();
  assert.deepStrictEqual(tab.types(), [], 'still a first opening');
});

test('a page whose worker is refused on its way back still re-syncs', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);

  tab.windowHandlers.pagehide({ persisted: true });
  tab.refusesSharedWorkers = true;
  tab.windowHandlers.pageshow({ persisted: true });
  await settle();

  assert.equal(tab.sources.length, 1);
  assert.deepStrictEqual(tab.types(), ['sse:reconnect']);
});

test('a worker reporting an error leaves the page a stream of its own', async () => {
  const browser = new FakeBrowser();
  browser.workersStart = false;
  const [tab] = await openTabs(browser, 1);

  tab.sharedWorkers[0].onerror();

  assert.equal(tab.sources.length, 1);
  tab.timers.advance(ANSWER_TIMEOUT_MS);
  assert.equal(tab.sources.length, 1, 'the wait for an answer ended with it');
});

test('a page left without a worker resumes its own stream where the shared one stopped', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);
  const worker = browser.worker();
  worker.stream().deliver(OPERATION, 'cursor-7');
  await settle();
  tab.events.length = 0;

  browser.kill(worker);
  tab.refusesSharedWorkers = true;
  tab.timers.advance(CHECK_INTERVAL_MS);
  tab.timers.advance(ANSWER_TIMEOUT_MS);
  await settle();

  assert.equal(tab.stream().url, '/api/v1/stream?last_event_id=cursor-7');
  assert.deepStrictEqual(tab.types(), ['sse:reconnect']);

  tab.stream().deliver(OPERATION, 'cursor-8');
  assert.deepStrictEqual(tab.types(), ['sse:reconnect', 'sse:files.operation']);
});

test('without shared workers every tab keeps a stream of its own', async () => {
  const browser = new FakeBrowser();
  const [first, second] = await openTabs(browser, 2, { sharedWorker: false });

  assert.equal(browser.workers.size, 0);
  assert.equal(first.sources.length, 1);
  assert.equal(second.sources.length, 1);
  assert.deepStrictEqual(second.fetches, []);

  first.stream().deliver(OPERATION);
  await settle();
  assert.deepStrictEqual(second.types(), []);
});

test('a stream of its own is opened again when the browser gave up on it', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1, { sharedWorker: false });

  tab.stream().fail();
  tab.timers.advance(RETRY_MS);

  assert.equal(tab.sources.length, 2);
});

test('a stream of its own that closed waits for the page to be shown', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1, { sharedWorker: false });
  tab.stream().close();

  tab.hide();
  assert.equal(tab.sources.length, 1);

  tab.show();
  assert.equal(tab.sources.length, 2);
});

test('a page that cannot tell which file it runs keeps a stream of its own', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1, { currentScript: false });

  assert.equal(tab.sharedWorkers.length, 0, 'there is no file to start a worker from');
  assert.equal(tab.sources.length, 1);
});

test('a page that names no user keeps a stream of its own', async () => {
  const browser = new FakeBrowser();
  const [first, second] = await openTabs(browser, 2, { userId: null });

  assert.equal(browser.workers.size, 0);
  assert.equal(first.sources.length, 1);
  assert.equal(second.sources.length, 1);
});
