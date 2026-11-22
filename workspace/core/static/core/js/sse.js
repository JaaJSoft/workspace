// Global Server-Sent Events stream + module registry.
//
// Reads the modules registry from <script id="workspace-modules-data" type="application/json">
// (rendered via Django's |json_script filter). For each active module the
// registry holds {slug, name, icon, color} so notification cards can
// reference it by slug.
//
// Re-dispatches each SSE payload as a window CustomEvent named "sse:<event>",
// plus dash-separated aliases ("chat-<event>", "ai-stream-<event>") for
// events whose name starts with "chat." or "ai_stream.": Alpine's
// @event.window syntax parses dots as modifiers, so templates need a
// dot-free event name to bind to.
//
// One stream per browser. This file runs in two places: in every page, and in
// a shared worker the pages of a user start from it. The worker opens the
// stream and posts its events to each page, which dispatches them; it lives
// as long as one of them does, so a page that goes takes nothing with it.
// Listeners cannot tell where an event came from. Without shared workers, a
// page opens the stream itself.
(function () {
  const STREAM_URL = '/api/v1/stream';
  const SNAPSHOT_URL = '/api/v1/stream/snapshot';
  // Before a stream the browser gave up on is opened again.
  const RETRY_MS = 30000;

  function withCursor(url, cursor) {
    return cursor ? url + '?last_event_id=' + encodeURIComponent(cursor) : url;
  }

  // The stream, in whichever of the two places it runs. `onEvent(id, payload)`
  // gets each event with the resume point it leaves the stream at, `onReopen`
  // is told when a stream opens again after events may have been missed.
  function openStream(cursor, onEvent, onReopen) {
    let source = null;
    let errorCount = 0;
    let retryTimer = null;
    // EventSource replays the last id on its own reconnects, but a stream
    // opened again from here is a brand new EventSource with no id to replay:
    // it goes in the URL, and the server reads it from there.
    let lastEventId = cursor || '';
    // The first opening is silent: the page has just rendered, and listeners
    // that re-fetch on a reopening would do so for nothing.
    let firstOpening = true;

    function close() {
      clearTimeout(retryTimer);
      retryTimer = null;
      if (source) { source.close(); source = null; }
    }

    function connect() {
      close();
      const opened = new EventSource(withCursor(STREAM_URL, lastEventId));
      source = opened;
      errorCount = 0;
      opened.addEventListener('sse', function (e) {
        if (e.lastEventId) lastEventId = e.lastEventId;
        onEvent(lastEventId, JSON.parse(e.data));
      });
      opened.onerror = function () {
        if (source !== opened) return;
        // Whatever opens after a failure opens late.
        firstOpening = false;
        // CLOSED: an answer the browser never retries after, which is anything
        // but a 200 event stream - a 502 while the server restarts, for one.
        // Otherwise it reconnects on its own, until that failed too often.
        if (opened.readyState === EventSource.CLOSED || ++errorCount > 5) {
          close();
          retryTimer = setTimeout(connect, RETRY_MS);
        }
      };
      opened.onopen = function () {
        errorCount = 0;
        if (!firstOpening) onReopen();
        firstOpening = false;
      };
    }

    connect();
    return {
      close,
      reopen: connect,
      isClosed: function () { return !source || source.readyState === EventSource.CLOSED; },
      cursor: function () { return lastEventId; },
    };
  }

  // -- In the shared worker ------------------------------------------------

  function serveTabs() {
    const ports = new Set();
    let stream = null;

    function toAll(message) {
      for (const port of ports) port.postMessage(message);
    }

    self.onconnect = function (connection) {
      const port = connection.ports[0];
      port.onmessage = function (e) {
        const message = e.data;
        if (message.type === 'bye') {
          ports.delete(port);
          // Nobody listens any more. Pages can outlast that (in the
          // back/forward cache) and the worker with them: it then holds no
          // stream, and the next page starts one.
          if (!ports.size && stream) { stream.close(); stream = null; }
          return;
        }
        if (message.type === 'hello') ports.add(port);
        const fresh = !stream;
        if (fresh) {
          // The page starting the stream says where its last one stopped.
          stream = openStream(
            message.id,
            function (id, payload) { toAll({ type: 'event', id, payload }); },
            function () { toAll({ type: 'reconnect' }); },
          );
        } else if (message.shown && stream.isClosed()) {
          // Someone has just opened or shown a page: a stream that closed is
          // opened again without waiting for its retry.
          stream.reopen();
        }
        port.postMessage({
          type: message.type === 'hello' ? 'welcome' : 'alive',
          fresh,
          id: stream.cursor(),
        });
      };
    };
  }

  if (typeof SharedWorkerGlobalScope !== 'undefined') {
    serveTabs();
    return;
  }

  // -- In a page -------------------------------------------------------------

  // Module registry (slug -> {name, icon, color})
  window.__modules = {};
  const modulesEl = document.getElementById('workspace-modules-data');
  if (modulesEl) {
    try {
      const modules = JSON.parse(modulesEl.textContent);
      for (const m of modules) {
        window.__modules[m.slug] = { name: m.name, icon: m.icon, color: m.color };
      }
    } catch (e) {
      console.error('Failed to parse workspace modules data', e);
    }
  }

  // How long the worker has to answer before the page does without it.
  const ANSWER_TIMEOUT_MS = 5000;
  // How often a visible page checks that the worker is still there.
  const CHECK_INTERVAL_MS = 20000;
  // Events wait for the snapshot, so it cannot be waited for without limit.
  const SNAPSHOT_TIMEOUT_MS = 5000;

  // The worker is started from the URL this page loaded the file at, which
  // changes with its content once static files are hashed: a page never talks
  // to a worker running another version. (Not in development, where a worker
  // keeps the old code until every tab is closed.)
  const scriptUrl = document.currentScript && document.currentScript.src;
  const userId = document.body && document.body.dataset.userId;
  let sharing = Boolean(window.SharedWorker && scriptUrl && userId);
  // Named after the user: a tab still showing another account's page must not
  // be handed this one's events.
  const workerName = 'workspace-sse:' + userId;

  // Where the stream stands, to resume it from: sent to a worker this page
  // starts, to the snapshot, and to the stream the page falls back to.
  let lastEventId = '';
  // The page's own stream, when it shares none.
  let stream = null;
  let port = null;
  let answerTimer = null;
  let checkTimer = null;
  // Events held back while the snapshot loads; null once it has.
  let buffered = null;

  function dispatch(payload) {
    window.dispatchEvent(new CustomEvent('sse:' + payload.event, { detail: payload.data }));
    if (payload.event.startsWith('chat.')) {
      window.dispatchEvent(new CustomEvent('chat-' + payload.event.substring(5), { detail: payload.data }));
    }
    if (payload.event.startsWith('ai_stream.')) {
      window.dispatchEvent(new CustomEvent('ai-stream-' + payload.event.substring(10), { detail: payload.data }));
    }
  }

  // The stream was opened again, or this page changed streams: pushes may
  // have been missed meanwhile - let listeners re-sync.
  function announceReconnect() {
    window.dispatchEvent(new CustomEvent('sse:reconnect'));
  }

  function openOwnStream() {
    stream = openStream(lastEventId, function (id, payload) {
      lastEventId = id;
      dispatch(payload);
    }, announceReconnect);
  }

  // A page that joins a stream already open never got the events a stream
  // starts with (unread counts, generations under way): it asks the server,
  // with its cursor when it comes back to a stream it had left. Events wait
  // for the answer and are applied after it, the order a stream would have
  // delivered them in.
  function loadSnapshot() {
    const held = [];
    buffered = held;
    const tooLong = new AbortController();
    const giveUp = setTimeout(function () { tooLong.abort(); }, SNAPSHOT_TIMEOUT_MS);
    fetch(withCursor(SNAPSHOT_URL, lastEventId), { credentials: 'same-origin', signal: tooLong.signal })
      .then((response) => (response.ok ? response.json() : null))
      .catch(() => null)
      .then((snapshot) => {
        clearTimeout(giveUp);
        // The page left this stream meanwhile.
        if (buffered !== held) return;
        buffered = null;
        const events = snapshot && Array.isArray(snapshot.events) ? snapshot.events : [];
        for (const payload of events) dispatch(payload);
        for (const payload of held) dispatch(payload);
      });
  }

  function onWorkerMessage(message) {
    // Whatever it says, the worker is there.
    clearTimeout(answerTimer);
    answerTimer = null;
    // A worker this page started is opening the stream now, and its first
    // events are on their way. One already running sent them long ago: they
    // are asked for from where this page stopped, before it takes the
    // worker's cursor for its own.
    if (message.type === 'welcome' && !message.fresh) loadSnapshot();
    if (message.id) lastEventId = message.id;
    if (message.type === 'event') {
      if (buffered) buffered.push(message.payload);
      else dispatch(message.payload);
    } else if (message.type === 'reconnect') {
      // While its snapshot loads, the page has just rendered or re-synced.
      if (!buffered) announceReconnect();
    }
  }

  function expectAnswer(otherwise) {
    if (answerTimer) return;
    answerTimer = setTimeout(function () {
      answerTimer = null;
      otherwise();
    }, ANSWER_TIMEOUT_MS);
  }

  function leave() {
    clearTimeout(answerTimer);
    clearInterval(checkTimer);
    answerTimer = null;
    checkTimer = null;
    buffered = null;
    if (!port) return;
    port.postMessage({ type: 'bye' });
    port.close();
    port = null;
  }

  // No worker to be had: back to a stream of the page's own.
  function goSolo() {
    leave();
    sharing = false;
    openOwnStream();
  }

  function rejoin() {
    leave();
    join(true);
  }

  // The browser can end a worker without telling its pages (a phone short of
  // memory). A visible page asks now and then, and starts over when nothing
  // answers. `shown` when someone has just opened or shown the page.
  function check(shown) {
    if (!port || document.hidden) return;
    port.postMessage({ type: 'check', shown: Boolean(shown) });
    expectAnswer(rejoin);
  }

  function join(resumed) {
    if (port) return;
    if (resumed) announceReconnect();
    let worker;
    try {
      worker = new SharedWorker(scriptUrl, { name: workerName });
    } catch (e) {
      goSolo();
      return;
    }
    const joined = worker.port;
    port = joined;
    worker.onerror = function () { if (port === joined) goSolo(); };
    joined.onmessage = function (e) { if (port === joined) onWorkerMessage(e.data); };
    joined.postMessage({ type: 'hello', id: lastEventId, shown: !document.hidden });
    // A worker that failed to start says nothing.
    expectAnswer(goSolo);
    checkTimer = setInterval(check, CHECK_INTERVAL_MS);
  }

  // Reconnect SSE when the page becomes visible again (mobile resume)
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) return;
    if (sharing) check(true);
    else if (stream.isClosed()) stream.reopen();
  });

  // A bfcache restore (mobile back/forward) resumes a frozen page: no script
  // re-runs, no request goes out, and the EventSource can still report OPEN
  // while its underlying connection is already dead - so the readyState check
  // above would skip the reconnect and leave the page on stale data forever.
  // Reconnect unconditionally instead, and listeners are told to re-sync.
  window.addEventListener('pageshow', function (e) {
    if (!e.persisted) return;
    if (sharing) join(true);
    else stream.reopen();
  });

  if (sharing) {
    // A page the browser sets aside (back/forward cache, frozen tab) runs no
    // script and must be sent nothing: it leaves on the way out, and joins
    // again when it comes back.
    window.addEventListener('pagehide', function () { if (sharing) leave(); });
    document.addEventListener('freeze', function () { if (sharing) leave(); });
    document.addEventListener('resume', function () { if (sharing) join(true); });
    join(false);
  } else {
    openOwnStream();
  }
})();
