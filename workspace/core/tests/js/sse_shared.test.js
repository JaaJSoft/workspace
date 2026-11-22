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

const RETRY_MIN_MS = 30000;
const RETRY_MAX_MS = 300000;
const ANSWER_TIMEOUT_MS = 5000;
const CHECK_INTERVAL_MS = 20000;
const SNAPSHOT_TIMEOUT_MS = 5000;

// What a page of user 7 asks the server for.
const STREAM = '/api/v1/stream?user=7';
const SNAPSHOT = '/api/v1/stream/snapshot?user=7';

function from(url, cursor) {
  return `${url}&last_event_id=${cursor}`;
}

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

function answering(events, complete = true) {
  return { ok: true, json: () => Promise.resolve({ events, complete }) };
}

const REFUSED = { ok: false, json: () => Promise.resolve({ events: [] }) };

/** A tab whose snapshot requests are answered by the test, one after the other. */
function tabAnsweredByHand(browser) {
  const answers = [];
  const tab = browser.openTab({
    snapshot: () => {
      const answer = deferred();
      answers.push(answer);
      return answer.promise;
    },
  });
  return { tab, answers };
}

/** The tab checks on its worker, and waits for an answer as long as it is willing to. */
async function checkAndWait(tab) {
  tab.timers.advance(CHECK_INTERVAL_MS);
  await settle();
  tab.timers.advance(ANSWER_TIMEOUT_MS);
  await settle();
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
  assert.equal(worker.stream().url, STREAM);
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

test('tabs of two accounts share nothing, and each stream names its own', async () => {
  const browser = new FakeBrowser();
  const mine = browser.openTab({ userId: '7' });
  const theirs = browser.openTab({ userId: '8' });
  await settle();

  assert.equal(browser.workers.size, 2);
  const [myWorker, theirWorker] = [mine, theirs].map(
    (tab) => [...browser.workers.values()].find((worker) => worker.tabs.has(tab)),
  );
  // The server refuses a stream to anyone but the user it names.
  assert.equal(myWorker.stream().url, '/api/v1/stream?user=7');
  assert.equal(theirWorker.stream().url, '/api/v1/stream?user=8');

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
  assert.deepStrictEqual(second.fetches, [SNAPSHOT]);
  assert.deepStrictEqual(second.events, [
    { type: 'sse:notifications.count', detail: { unread: 4 } },
  ]);
});

test('events wait for the snapshot and are applied after it', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const { tab, answers } = tabAnsweredByHand(browser);
  await settle();

  browser.worker().stream().deliver(COUNT_5);
  await settle();
  assert.deepStrictEqual(tab.events, [], 'an event must not land ahead of the snapshot');

  answers[0].resolve(answering([COUNT_4]));
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
  const { tab, answers } = tabAnsweredByHand(browser);
  await settle();

  // The stream reconnects on its own, and sends its first events again.
  stream.open();
  stream.deliver(COUNT_5);
  await settle();
  answers[0].resolve(answering([COUNT_4]));
  await settle();

  assert.deepStrictEqual(first.types(), ['sse:reconnect', 'sse:notifications.count']);
  assert.deepStrictEqual(tab.types(), ['sse:notifications.count', 'sse:notifications.count']);
});

test('a refused snapshot leaves the events flowing', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const { tab, answers } = tabAnsweredByHand(browser);
  await settle();
  browser.worker().stream().deliver(OPERATION);
  await settle();

  // An error page can carry a body: it is not a snapshot.
  answers[0].resolve({ ok: false, json: () => Promise.resolve({ events: [COUNT_4] }) });
  await settle();

  assert.deepStrictEqual(tab.types(), ['sse:files.operation']);
});

test('an answer that is no snapshot leaves the events flowing', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const { tab, answers } = tabAnsweredByHand(browser);
  await settle();
  browser.worker().stream().deliver(OPERATION);
  await settle();

  answers[0].resolve({ ok: true, json: () => Promise.resolve({}) });
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
  const { tab, answers } = tabAnsweredByHand(browser);
  await settle();

  tab.setAside();
  answers[0].resolve(answering([COUNT_4]));
  await settle();

  assert.deepStrictEqual(tab.types(), []);
});

test('a snapshot asked during an earlier stay does not answer for the next', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const { tab, answers } = tabAnsweredByHand(browser);
  await settle();

  // Away and back before the first answer came.
  tab.setAside();
  tab.bringBack();
  await settle();
  browser.worker().stream().deliver(OPERATION);
  await settle();
  tab.events.length = 0;

  answers[0].resolve(answering([COUNT_4]));
  await settle();
  assert.deepStrictEqual(tab.types(), [], 'neither applied, nor the end of the wait');

  answers[1].resolve(answering([COUNT_5]));
  await settle();
  assert.deepStrictEqual(tab.events, [
    { type: 'sse:notifications.count', detail: { unread: 5 } },
    { type: 'sse:files.operation', detail: {} },
  ]);
});

// -- a page that is not listening yet ------------------------------------------

test('events that beat the page to its listeners are kept for them', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  // The document is still being parsed when the script runs: Alpine, and the
  // stores that listen, start after it.
  const tab = browser.openTab({ parsing: true, snapshot: [COUNT_4] });
  await settle();
  browser.worker().stream().deliver(COUNT_5);
  await settle();
  assert.deepStrictEqual(tab.events, [], 'nobody is listening yet');

  tab.documentHandlers['alpine:initialized']();

  assert.deepStrictEqual(tab.events.map((event) => event.detail.unread), [4, 5]);

  browser.worker().stream().deliver(COUNT_4);
  await settle();
  assert.equal(tab.events.length, 3, 'from then on, at once');
});

test('a page where Alpine never starts listens once it has loaded, and once only', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const tab = browser.openTab({ parsing: true, snapshot: [COUNT_4] });
  await settle();

  tab.windowHandlers.load();
  tab.documentHandlers['alpine:initialized']();

  assert.deepStrictEqual(tab.types(), ['sse:notifications.count']);
});

// -- where a page stands, for the day it comes back ---------------------------

test('a page coming back asks what was queued since the last event it applied', async () => {
  const browser = new FakeBrowser();
  const [staying, away] = await openTabs(browser, 2);
  const worker = browser.worker();
  worker.stream().deliver(OPERATION, 'cursor-7');
  await settle();

  away.setAside();
  await settle();
  worker.stream().deliver(OPERATION, 'cursor-8');
  await settle();
  away.bringBack();
  await settle();

  assert.equal(away.lastFetch(), from(SNAPSHOT, 'cursor-7'));
  assert.equal(staying.fetches.length, 0);
});

test('a tab that saw no event learns from the worker where the stream stands', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  browser.worker().stream().deliver(OPERATION, 'cursor-7');
  const [late] = await openTabs(browser, 1);

  late.setAside();
  late.bringBack();
  await settle();

  assert.deepStrictEqual(late.fetches, [SNAPSHOT, from(SNAPSHOT, 'cursor-7')]);
});

test('an event held for a snapshot the page never read is asked for again', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const worker = browser.worker();
  const { tab, answers } = tabAnsweredByHand(browser);
  await settle();
  answers[0].resolve(answering([]));
  worker.stream().deliver(OPERATION, 'cursor-7');
  await settle();

  // Back, and set aside again while the snapshot loads: the event that
  // arrived meanwhile was held, and is dropped with the wait.
  tab.setAside();
  tab.bringBack();
  await settle();
  worker.stream().deliver(OPERATION, 'cursor-9');
  await settle();
  tab.setAside();
  tab.bringBack();
  await settle();

  assert.equal(tab.lastFetch(), from(SNAPSHOT, 'cursor-7'), 'not from an event it never dispatched');
});

test('a snapshot that failed leaves the page where it stood', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const worker = browser.worker();
  const { tab, answers } = tabAnsweredByHand(browser);
  await settle();
  answers[0].resolve(answering([]));
  worker.stream().deliver(OPERATION, 'cursor-7');
  await settle();

  tab.setAside();
  await settle();
  worker.stream().deliver(OPERATION, 'cursor-8');
  await settle();
  tab.bringBack();
  await settle();
  answers[1].resolve(REFUSED);
  await settle();

  // Events keep coming, and must not carry the page past what it is owed.
  tab.events.length = 0;
  worker.stream().deliver(OPERATION, 'cursor-9');
  await settle();
  assert.deepStrictEqual(tab.types(), ['sse:files.operation']);

  tab.setAside();
  tab.bringBack();
  await settle();

  assert.equal(tab.lastFetch(), from(SNAPSHOT, 'cursor-7'), 'what the failed answer held is still owed');
});

test('a page whose snapshot failed asks again at its next check, and moves on once it came', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const worker = browser.worker();
  const { tab, answers } = tabAnsweredByHand(browser);
  await settle();
  answers[0].resolve(REFUSED);
  worker.stream().deliver(OPERATION, 'cursor-7');
  await settle();
  tab.events.length = 0;

  tab.timers.advance(CHECK_INTERVAL_MS);
  await settle();
  assert.equal(tab.fetches.length, 2);
  assert.equal(tab.lastFetch(), SNAPSHOT, 'from where it stood, which was nowhere yet');

  answers[1].resolve(answering([COUNT_4]));
  await settle();
  assert.deepStrictEqual(tab.types(), ['sse:notifications.count']);

  // Caught up: its cursor follows the events again, and nothing more is asked.
  worker.stream().deliver(OPERATION, 'cursor-8');
  await settle();
  tab.timers.advance(CHECK_INTERVAL_MS);
  await settle();
  assert.equal(tab.fetches.length, 2);
  tab.setAside();
  tab.bringBack();
  await settle();
  assert.equal(tab.lastFetch(), from(SNAPSHOT, 'cursor-8'));
});

test('a snapshot that came on asking again brings the page up to the stream', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  browser.worker().stream().deliver(OPERATION, 'cursor-7');
  const { tab, answers } = tabAnsweredByHand(browser);
  await settle();
  answers[0].resolve(REFUSED);
  await settle();

  tab.timers.advance(CHECK_INTERVAL_MS);
  await settle();
  answers[1].resolve(answering([]));
  await settle();
  tab.setAside();
  tab.bringBack();
  await settle();

  assert.equal(tab.lastFetch(), from(SNAPSHOT, 'cursor-7'), 'no need to ask for all of it again');
});

test('a snapshot that came short is applied, and asked again', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const worker = browser.worker();
  worker.stream().deliver(OPERATION, 'cursor-7');
  const { tab, answers } = tabAnsweredByHand(browser);
  await settle();

  // A provider failed on the server: the others answered.
  answers[0].resolve(answering([COUNT_4], false));
  await settle();
  assert.deepStrictEqual(tab.types(), ['sse:notifications.count']);

  tab.timers.advance(CHECK_INTERVAL_MS);
  await settle();
  assert.equal(tab.fetches.length, 2);

  // Had it taken the stream's position for its own, it would ask from there.
  assert.equal(tab.lastFetch(), SNAPSHOT);
});

test('a hidden page waits to be on screen before asking again', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const { tab, answers } = tabAnsweredByHand(browser);
  await settle();
  answers[0].resolve(REFUSED);
  await settle();

  tab.hide();
  tab.timers.advance(CHECK_INTERVAL_MS);
  await settle();
  assert.equal(tab.fetches.length, 1);

  tab.show();
  await settle();
  assert.equal(tab.fetches.length, 2);
});

test('an event without an id leaves the page where it stood', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);
  // A second page that never got anywhere: its snapshot was refused.
  const { tab: nowhere, answers } = tabAnsweredByHand(browser);
  await settle();
  answers[0].resolve(REFUSED);
  browser.worker().stream().deliver(OPERATION, 'cursor-7');
  await settle();

  // The worker ends, and that page is the one to start the next: its stream
  // has no position, and neither have the events it sends.
  browser.kill(browser.worker());
  await checkAndWait(nowhere);
  assert.equal(browser.worker().stream().url, STREAM);
  await checkAndWait(tab);
  browser.worker().stream().deliver(OPERATION);
  await settle();
  tab.setAside();
  tab.bringBack();
  await settle();

  assert.equal(tab.lastFetch(), from(SNAPSHOT, 'cursor-7'));
});

test('a page asks less and less often for a snapshot that keeps coming short', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const { tab, answers } = tabAnsweredByHand(browser);
  await settle();

  async function refusedAfter(checks) {
    answers[answers.length - 1].resolve(REFUSED);
    await settle();
    for (let i = 0; i < checks; i += 1) {
      tab.timers.advance(CHECK_INTERVAL_MS);
      await settle();
    }
    return tab.fetches.length;
  }

  // A provider down for long: every tab asking every 20 seconds would get
  // the same queued events each time.
  assert.equal(await refusedAfter(1), 2, 'at the next check');
  assert.equal(await refusedAfter(1), 2);
  assert.equal(await refusedAfter(1), 3, 'then after two');
  assert.equal(await refusedAfter(3), 3);
  assert.equal(await refusedAfter(1), 4, 'then after four');

  for (let i = 0; i < 4; i += 1) await refusedAfter(15);
  const before = tab.fetches.length;
  assert.equal(await refusedAfter(14), before);
  assert.equal(await refusedAfter(1), before + 1, 'never more than fifteen');
});

test('the time a page spends hidden counts towards its wait', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const { tab, answers } = tabAnsweredByHand(browser);
  await settle();
  answers[0].resolve(REFUSED);
  await settle();
  tab.timers.advance(CHECK_INTERVAL_MS);
  await settle();
  answers[1].resolve(REFUSED);
  await settle();
  assert.equal(tab.fetches.length, 2);

  // Two checks to wait, spent in the background.
  tab.hide();
  for (let i = 0; i < 2; i += 1) {
    tab.timers.advance(CHECK_INTERVAL_MS);
    await settle();
  }
  assert.equal(tab.fetches.length, 2, 'not while hidden');

  tab.show();
  await settle();
  assert.equal(tab.fetches.length, 3, 'at once when shown');
});

test('a snapshot that came puts an end to the waiting', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const { tab, answers } = tabAnsweredByHand(browser);
  await settle();
  for (let failures = 0; failures < 3; failures += 1) {
    answers[answers.length - 1].resolve(REFUSED);
    await settle();
    for (let i = 0; i < 4 && tab.fetches.length === failures + 1; i += 1) {
      tab.timers.advance(CHECK_INTERVAL_MS);
      await settle();
    }
  }
  answers[answers.length - 1].resolve(answering([]));
  await settle();

  // Owed again later, it starts over at the next check.
  tab.setAside();
  tab.bringBack();
  await settle();
  answers[answers.length - 1].resolve(REFUSED);
  await settle();
  const asked = tab.fetches.length;
  tab.timers.advance(CHECK_INTERVAL_MS);
  await settle();

  assert.equal(tab.fetches.length, asked + 1);
});

test('a page still owed a snapshot is caught up by the stream it starts', async () => {
  const browser = new FakeBrowser();
  const [first] = await openTabs(browser, 1);
  const { tab, answers } = tabAnsweredByHand(browser);
  await settle();
  answers[0].resolve(answering([]));
  browser.worker().stream().deliver(OPERATION, 'cursor-7');
  await settle();
  tab.setAside();
  tab.bringBack();
  await settle();
  answers[1].resolve(REFUSED);
  await settle();

  // Left alone, it starts the next stream from where it stood: nothing is
  // owed any more, and its cursor follows the events again.
  first.windowHandlers.pagehide({ persisted: false });
  browser.close(first);
  tab.setAside();
  await settle();
  tab.bringBack();
  await settle();
  assert.equal(browser.worker().stream().url, from(STREAM, 'cursor-7'));
  browser.worker().stream().deliver(OPERATION, 'cursor-9');
  await settle();
  tab.setAside();
  await settle();
  tab.bringBack();
  await settle();

  assert.equal(browser.worker().stream().url, from(STREAM, 'cursor-9'));
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

  worker.timers.advance(RETRY_MIN_MS);
  assert.equal(worker.sources.length, 2);
  assert.equal(worker.stream().url, from(STREAM, 'cursor-7'));

  worker.stream().open();
  await settle();
  assert.deepStrictEqual(tab.types(), ['sse:reconnect']);
});

test('an event without an id leaves the resume point of the stream alone', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const worker = browser.worker();
  worker.stream().deliver(OPERATION, 'cursor-7');
  worker.stream().fail();
  worker.timers.advance(RETRY_MIN_MS);

  // A new connection has no last id of its own until an event brings one.
  worker.stream().deliver({ event: 'chat.typing', data: {} });
  worker.stream().fail();
  worker.timers.advance(2 * RETRY_MIN_MS);

  assert.equal(worker.sources.length, 3);
  assert.equal(worker.stream().url, from(STREAM, 'cursor-7'));
});

test('each failure in a row doubles the wait, up to a limit, and an opening resets it', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1, { hidden: true });
  const worker = browser.worker();

  worker.stream().fail();
  worker.timers.advance(RETRY_MIN_MS);
  assert.equal(worker.sources.length, 2);

  worker.stream().fail();
  worker.timers.advance(RETRY_MIN_MS);
  assert.equal(worker.sources.length, 2, 'twice as long the second time');
  worker.timers.advance(RETRY_MIN_MS);
  assert.equal(worker.sources.length, 3);

  for (let i = 0; i < 6; i += 1) {
    worker.stream().fail();
    worker.timers.advance(RETRY_MAX_MS);
  }
  assert.equal(worker.sources.length, 9, 'never longer than the limit');

  worker.stream().open();
  worker.stream().fail();
  worker.timers.advance(RETRY_MIN_MS);
  assert.equal(worker.sources.length, 10);
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
  worker.timers.advance(RETRY_MIN_MS);
  assert.equal(worker.sources.length, 2);

  // The count starts over with the new stream.
  for (let i = 0; i < 5; i += 1) worker.stream().stumble();
  assert.equal(worker.stream().closed, false);
});

test('a connection that came back wipes the slate', async () => {
  const browser = new FakeBrowser();
  await openTabs(browser, 1);
  const stream = browser.worker().stream();

  // The server ends every stream after ten minutes: an error, then an opening.
  for (let i = 0; i < 12; i += 1) {
    stream.stumble();
    stream.open();
  }

  assert.equal(stream.closed, false);
  assert.equal(browser.worker().sources.length, 1);
});

test('an opening that follows a failure is announced, the first one included', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);
  const worker = browser.worker();

  // The page has been waiting for its stream since it rendered.
  worker.stream().fail();
  worker.timers.advance(RETRY_MIN_MS);
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
  tab.setAside();
  await settle();
  worker.timers.advance(RETRY_MAX_MS);

  assert.equal(worker.sources.length, 1);
  assert.equal(worker.timers.pending(), 0);
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

  worker.stream().fail();
  tab.timers.advance(CHECK_INTERVAL_MS);
  await settle();
  assert.equal(worker.sources.length, 4, 'a page still on screen at its next check');
});

test('hidden pages leave a closed stream to its retry', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1, { hidden: true });
  const worker = browser.worker();
  worker.stream().fail();

  browser.openTab({ hidden: true });
  await settle();
  assert.equal(worker.sources.length, 1, 'a page opened in the background');

  tab.timers.advance(CHECK_INTERVAL_MS);
  await settle();
  assert.equal(worker.sources.length, 1, 'the check of a page nobody looks at');
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

test('a tab that goes costs the others nothing', async () => {
  const browser = new FakeBrowser();
  const [first, second, third] = await openTabs(browser, 3);
  const worker = browser.worker();
  worker.stream().open();

  // The tab that started the worker, closed without a word.
  browser.close(first);
  worker.stream().deliver(OPERATION);
  await settle();

  assert.deepStrictEqual(second.types(), ['sse:files.operation'], 'no re-sync either');
  assert.deepStrictEqual(third.types(), ['sse:files.operation']);
  assert.equal(worker.sources.length, 1);
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
  assert.deepStrictEqual(landed.fetches, [SNAPSHOT]);
  assert.deepStrictEqual(landed.types(), ['sse:notifications.count']);
});

test('the stream goes with the last tab listening, and comes back with the next', async () => {
  const browser = new FakeBrowser();
  const [first, second] = await openTabs(browser, 2);
  const worker = browser.worker();

  first.setAside();
  await settle();
  assert.equal(worker.stream().closed, false, 'someone still listens');

  second.setAside();
  await settle();
  assert.equal(worker.stream().closed, true, 'pages in the bfcache keep the worker, not a stream');

  const next = browser.openTab();
  await settle();
  assert.equal(worker.sources.length, 2);
  assert.equal(worker.stream().closed, false);
  assert.deepStrictEqual(next.fetches, [], 'it started this stream');
});

test('a page set aside is sent nothing, and keeps no timer running', async () => {
  const browser = new FakeBrowser();
  const [staying, aside] = await openTabs(browser, 2);

  aside.setAside();
  await settle();
  browser.worker().stream().deliver(OPERATION);
  await settle();

  assert.deepStrictEqual(staying.types(), ['sse:files.operation']);
  assert.deepStrictEqual(aside.types(), []);
  assert.equal(aside.timers.pending(), 0);
});

test('a page shown while it is set aside does nothing', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);

  tab.setAside();
  tab.show();
  await settle();

  assert.equal(tab.sharedWorkers.length, 1);
  assert.equal(tab.timers.pending(), 0);
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
    assert.deepStrictEqual(away.fetches, [SNAPSHOT, from(SNAPSHOT, 'cursor-7')]);
    assert.equal(worker.sources.length, 1, 'the stream never moved');
    assert.deepStrictEqual(staying.types(), ['sse:files.operation', 'sse:files.operation']);
  });
}

test('the only page, restored from the bfcache, resumes the stream where it stood', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);
  const worker = browser.worker();
  worker.stream().open();
  worker.stream().deliver(OPERATION, 'cursor-7');
  await settle();
  tab.events.length = 0;

  tab.setAside();
  await settle();
  tab.bringBack();
  await settle();

  assert.equal(worker.sources.length, 2);
  // The cursor names a position for every provider that had sent an event: a
  // stream started without it would forget them all.
  assert.equal(worker.stream().url, from(STREAM, 'cursor-7'));
  assert.deepStrictEqual(tab.fetches, [], 'the stream replays what it missed');
  assert.deepStrictEqual(tab.types(), [], 'nothing can reach it yet');

  // Told once the stream is there: what comes between a re-sync and the
  // opening would be in neither.
  worker.stream().open();
  await settle();
  assert.deepStrictEqual(tab.types(), ['sse:reconnect']);
});

test('a page that comes back twice is not handed the same events twice', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);
  const worker = browser.worker();
  worker.stream().deliver(OPERATION, 'cursor-7');
  await settle();

  tab.setAside();
  await settle();
  tab.bringBack();
  await settle();
  // What was queued while it was away, replayed by the stream it started.
  worker.stream().deliver({ event: 'files.lock_released', data: {} }, 'cursor-8');
  await settle();

  tab.setAside();
  await settle();
  tab.bringBack();
  await settle();

  assert.equal(worker.stream().url, from(STREAM, 'cursor-8'));
});

test('a restore that fires both resume and pageshow joins once', async () => {
  const browser = new FakeBrowser();
  const [, tab] = await openTabs(browser, 2);

  tab.setAside();
  tab.documentHandlers.freeze();
  await settle();
  tab.documentHandlers.resume();
  tab.bringBack();
  await settle();

  assert.equal(tab.sharedWorkers.length, 2, 'once at load, once on the way back');
  assert.deepStrictEqual(tab.types(), ['sse:reconnect']);

  tab.events.length = 0;
  browser.worker().stream().deliver(OPERATION);
  await settle();
  assert.deepStrictEqual(tab.types(), ['sse:files.operation'], 'a second port would hear it twice');
});

// -- a worker that is not there -----------------------------------------------

test('a worker the browser ended is replaced by the page that notices', async () => {
  const browser = new FakeBrowser();
  const [tab, other] = await openTabs(browser, 2);
  const worker = browser.worker();
  worker.stream().deliver(OPERATION, 'cursor-7');
  await settle();
  tab.events.length = 0;
  other.events.length = 0;

  browser.kill(worker);
  tab.timers.advance(CHECK_INTERVAL_MS);
  await settle();
  assert.equal(tab.sharedWorkers.length, 1, 'the worker gets a moment to answer');

  tab.timers.advance(ANSWER_TIMEOUT_MS);
  await settle();

  const replacement = browser.worker();
  assert.notEqual(replacement, worker);
  assert.equal(replacement.stream().url, from(STREAM, 'cursor-7'));
  assert.deepStrictEqual(tab.fetches, [], 'the stream it starts replays what it missed');

  replacement.stream().open();
  await settle();
  assert.deepStrictEqual(tab.types(), ['sse:reconnect'], 'it changed streams');

  replacement.stream().deliver(OPERATION, 'cursor-8');
  await settle();
  assert.deepStrictEqual(tab.types(), ['sse:reconnect', 'sse:files.operation']);

  // The other tab finds out the same way, and joins the same worker.
  await checkAndWait(other);
  assert.equal(browser.worker(), replacement);
  assert.equal(replacement.sources.length, 1);
  assert.deepStrictEqual(other.types(), ['sse:reconnect']);
  assert.equal(other.lastFetch(), from(SNAPSHOT, 'cursor-7'));
});

test('a hidden page notices too: it rings and counts unread like the others', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1, { hidden: true });
  const worker = browser.worker();

  browser.kill(worker);
  await checkAndWait(tab);

  assert.notEqual(browser.worker(), worker);
  assert.equal(tab.sharedWorkers.length, 2);
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

test('a worker that never starts leaves the page a stream of its own', async () => {
  const browser = new FakeBrowser();
  browser.workersStart = false;
  const [tab] = await openTabs(browser, 1);
  assert.equal(tab.sources.length, 0);

  tab.timers.advance(ANSWER_TIMEOUT_MS);
  await settle();

  assert.equal(tab.sources.length, 1);
  assert.equal(tab.stream().url, STREAM);
  tab.stream().open();
  assert.deepStrictEqual(tab.types(), ['sse:reconnect'], 'the page rendered a while ago by now');

  // From there on it behaves as a browser without shared workers does.
  tab.setAside();
  assert.equal(tab.stream().closed, false);
  tab.documentHandlers.resume();
  tab.bringBack();
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

test('a page whose worker is refused on its way back still re-syncs, once', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);

  tab.setAside();
  tab.refusesSharedWorkers = true;
  tab.bringBack();
  await settle();

  assert.equal(tab.sources.length, 1);
  tab.stream().open();
  assert.deepStrictEqual(tab.types(), ['sse:reconnect']);
});

test('a worker whose script did not load leaves the page a stream of its own', async () => {
  const browser = new FakeBrowser();
  browser.workersStart = false;
  const [tab] = await openTabs(browser, 1);

  tab.sharedWorkers[0].onerror();

  assert.equal(tab.sources.length, 1);
  tab.stream().open();
  assert.deepStrictEqual(tab.types(), [], 'it failed at once: still a first opening');
  tab.timers.advance(ANSWER_TIMEOUT_MS);
  assert.equal(tab.sources.length, 1, 'the wait for an answer ended with it');
});

test('a page whose worker fails to load on its way back is told when its own stream opens', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);
  tab.setAside();
  await settle();

  browser.kill(browser.worker());
  browser.workersStart = false;
  tab.bringBack();
  tab.sharedWorkers[1].onerror();

  assert.equal(tab.sources.length, 1);
  assert.deepStrictEqual(tab.types(), []);
  tab.stream().open();
  assert.deepStrictEqual(tab.types(), ['sse:reconnect']);
});

test('an error reported once the worker has answered is not a reason to leave it', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);

  // Firefox reports there what a running worker failed to catch.
  const report = tab.sharedWorkers[0].onerror;
  if (report) report();
  await settle();

  assert.equal(tab.sources.length, 0);
});

test('an error from a worker the page left is not a reason to leave the next', async () => {
  const browser = new FakeBrowser();
  browser.workersStart = false;
  const [tab] = await openTabs(browser, 1);
  const left = tab.sharedWorkers[0];

  tab.setAside();
  tab.bringBack();
  left.onerror();

  assert.equal(tab.sources.length, 0);
});

test('a page left without a worker resumes its own stream where it stood', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1);
  const worker = browser.worker();
  worker.stream().deliver(OPERATION, 'cursor-7');
  await settle();
  tab.events.length = 0;

  browser.kill(worker);
  tab.refusesSharedWorkers = true;
  await checkAndWait(tab);

  assert.equal(tab.stream().url, from(STREAM, 'cursor-7'));
  assert.deepStrictEqual(tab.types(), [], 'nothing can reach it yet');

  tab.stream().open();
  assert.deepStrictEqual(tab.types(), ['sse:reconnect']);

  tab.stream().deliver(OPERATION, 'cursor-8');
  assert.deepStrictEqual(tab.types(), ['sse:reconnect', 'sse:files.operation']);
});

test('without shared workers every tab keeps a stream of its own', async () => {
  const browser = new FakeBrowser();
  const [first, second] = await openTabs(browser, 2, { sharedWorker: false });

  assert.equal(browser.workers.size, 0);
  assert.equal(first.stream().url, STREAM);
  assert.equal(second.stream().url, STREAM);
  assert.deepStrictEqual(second.fetches, []);

  first.stream().open();
  assert.deepStrictEqual(first.types(), [], 'the first opening is silent there too');

  first.stream().deliver(OPERATION);
  await settle();
  assert.deepStrictEqual(first.types(), ['sse:files.operation']);
  assert.deepStrictEqual(second.types(), []);
});

test('a stream of its own is opened again when the browser gave up on it', async () => {
  const browser = new FakeBrowser();
  const [tab] = await openTabs(browser, 1, { sharedWorker: false });

  tab.stream().fail();
  tab.timers.advance(RETRY_MIN_MS);

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

test('a page that names no user keeps a stream of its own, and names none', async () => {
  const browser = new FakeBrowser();
  const [first, second] = await openTabs(browser, 2, { userId: null });

  assert.equal(browser.workers.size, 0);
  assert.equal(first.stream().url, '/api/v1/stream');
  assert.equal(second.sources.length, 1);
});
