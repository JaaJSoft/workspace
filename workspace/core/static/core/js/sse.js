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
// Without shared workers, a page opens the stream itself.
(function () {
  const STREAM_URL = '/api/v1/stream';
  const SNAPSHOT_URL = '/api/v1/stream/snapshot';
  // Before a stream the browser gave up on is tried again. Doubled after each
  // failure in a row: with nobody looking, there is no hurry.
  const RETRY_MIN_MS = 30000;
  const RETRY_MAX_MS = 300000;

  // `user` is who the page shows. A tab outlives its session, and the server
  // refuses to answer for whoever signed in since.
  function withQuery(url, user, cursor) {
    const query = [];
    if (user) query.push('user=' + encodeURIComponent(user));
    if (cursor) query.push('last_event_id=' + encodeURIComponent(cursor));
    return query.length ? url + '?' + query.join('&') : url;
  }

  // The stream, in whichever of the two places it runs. `onEvent(id, payload)`
  // gets each event with the resume point it leaves the stream at, `onReopen`
  // is told when a stream opens again after events may have been missed.
  function openStream(start, onEvent, onReopen) {
    let source = null;
    let errorCount = 0;
    let retryTimer = null;
    let retryDelay = RETRY_MIN_MS;
    // EventSource replays the last id on its own reconnects, but a stream
    // opened again from here is a brand new EventSource with no id to replay:
    // it goes in the URL, and the server reads it from there.
    let lastEventId = start.cursor || '';
    // The first opening is silent when the page has just rendered: listeners
    // that re-fetch on a reopening would do so for nothing.
    let firstOpening = !start.late;

    function close() {
      clearTimeout(retryTimer);
      retryTimer = null;
      if (source) { source.close(); source = null; }
    }

    function connect() {
      close();
      const opened = new EventSource(withQuery(STREAM_URL, start.user, lastEventId));
      source = opened;
      errorCount = 0;
      opened.addEventListener('sse', function (e) {
        // Empty on a new connection until an event carries an id.
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
          retryTimer = setTimeout(connect, retryDelay);
          retryDelay = Math.min(retryDelay * 2, RETRY_MAX_MS);
        }
      };
      opened.onopen = function () {
        errorCount = 0;
        retryDelay = RETRY_MIN_MS;
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
          // Where the page that starts it stood. The cursor names a position
          // for each provider that sent an event on the stream it comes from,
          // and a stream opened without one forgets them all. A page coming
          // back is told when it opens: it missed what came until then.
          stream = openStream(
            { user: message.user, cursor: message.cursor, late: message.returning },
            function (id, payload) { toAll({ type: 'event', id, payload }); },
            function () { toAll({ type: 'reconnect' }); },
          );
        } else if (message.visible && stream.isClosed()) {
          // Someone is looking at a page: a stream that closed is opened
          // again without waiting for its retry.
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
  // How often a page checks that the worker is still there.
  const CHECK_INTERVAL_MS = 20000;
  // Events wait for the snapshot, so it cannot be waited for without limit.
  const SNAPSHOT_TIMEOUT_MS = 5000;
  // The most checks a page lets pass before asking again for a snapshot.
  const MAX_PATIENCE = 15;

  // The worker is started from the URL this page loaded the file at, which
  // changes with its content once static files are hashed: a page never talks
  // to a worker running another version. (Not in development, where a worker
  // keeps the old code until every tab is closed.)
  const scriptUrl = document.currentScript && document.currentScript.src;
  const userId = document.body && document.body.dataset.userId;
  let sharing = Boolean(window.SharedWorker && scriptUrl && userId);
  const workerName = 'workspace-sse:' + userId;

  // The resume point of what this page applied: where a stream it starts
  // resumes, its own or the worker's, and what it asks the snapshot from.
  let lastEventId = '';
  // The page's own stream, when it shares none.
  let stream = null;
  let port = null;
  // Whether the page joined on its way back, as opposed to on loading.
  let returning = false;
  let answerTimer = null;
  let checkTimer = null;
  // Events held back while the snapshot loads; null once it has.
  let buffered = null;
  // Whether the page is still owed what it missed: its snapshot did not come,
  // or came short. Until one does, its cursor stays where it stood whatever
  // arrives, so that asking again still asks for all of it.
  let owed = false;
  // Checks of the worker to let pass before asking again, and those that
  // have. Doubled each time the snapshot comes short again: a provider down
  // for long would otherwise have every tab ask every 20 seconds, and get the
  // same queued events each time.
  let patience = 0;
  let waited = 0;

  // Listeners come with the scripts that follow this one and with Alpine,
  // which starts once the document is parsed. An event dispatched before
  // that finds nobody, and a snapshot can be that quick: events are kept
  // until the page listens.
  let listening = document.readyState !== 'loading';
  const early = [];

  function emit(type, detail) {
    if (listening) window.dispatchEvent(new CustomEvent(type, { detail }));
    else early.push([type, detail]);
  }

  function startListening() {
    listening = true;
    for (const [type, detail] of early.splice(0)) emit(type, detail);
  }

  if (!listening) {
    document.addEventListener('alpine:initialized', startListening);
    // Should Alpine never start.
    window.addEventListener('load', startListening);
  }

  function dispatch(payload) {
    emit('sse:' + payload.event, payload.data);
    if (payload.event.startsWith('chat.')) emit('chat-' + payload.event.substring(5), payload.data);
    if (payload.event.startsWith('ai_stream.')) emit('ai-stream-' + payload.event.substring(10), payload.data);
  }

  // The stream was opened again, or this page changed streams: pushes may
  // have been missed meanwhile - let listeners re-sync.
  function announceReconnect() {
    emit('sse:reconnect');
  }

  function apply(message) {
    if (message.id && !owed) lastEventId = message.id;
    dispatch(message.payload);
  }

  // `late` when the page rendered a while ago: it is then told of the
  // opening, having missed what came until then.
  function openOwnStream(late) {
    stream = openStream(
      { user: userId, cursor: lastEventId, late },
      function (id, payload) { dispatch(payload); },
      announceReconnect,
    );
  }

  // A page that joins a stream already open never got the events a stream
  // starts with (unread counts, generations under way), and one coming back
  // to it missed what was queued since: both ask the server, the second with
  // its cursor. Events wait for the answer and are applied after it, the
  // order a stream would have delivered them in. `streamCursor` is where the
  // stream stood when the page joined, which the answer brings the page up to.
  function loadSnapshot(streamCursor) {
    const held = [];
    buffered = held;
    const tooLong = new AbortController();
    const giveUp = setTimeout(function () { tooLong.abort(); }, SNAPSHOT_TIMEOUT_MS);
    fetch(withQuery(SNAPSHOT_URL, userId, lastEventId), { signal: tooLong.signal })
      .then((response) => (response.ok ? response.json() : null))
      .catch(() => null)
      .then((snapshot) => {
        clearTimeout(giveUp);
        // The page left this stream meanwhile, and may have joined it again.
        if (buffered !== held) return;
        buffered = null;
        const events = snapshot && Array.isArray(snapshot.events) ? snapshot.events : [];
        for (const payload of events) dispatch(payload);
        // Complete when every provider answered. Anything less is asked
        // again, from the same cursor (see onWorkerMessage).
        owed = !(snapshot && snapshot.complete);
        patience = owed ? Math.min(Math.max(1, patience * 2), MAX_PATIENCE) : 0;
        waited = 0;
        if (!owed && streamCursor) lastEventId = streamCursor;
        for (const message of held) {
          if (message.type === 'reconnect') announceReconnect();
          else apply(message);
        }
      });
  }

  function onWorkerMessage(message) {
    // Whatever it says, the worker is there.
    clearTimeout(answerTimer);
    answerTimer = null;
    if (message.type === 'welcome') {
      if (message.fresh) {
        // The stream this page started resumes where it stood: its first
        // events are on their way, and what the page missed with them.
        owed = false;
      } else {
        // Back next to a stream that kept running. A stream it starts tells
        // the page itself, on opening.
        if (returning) announceReconnect();
        loadSnapshot(message.id);
      }
    } else if (message.type === 'event') {
      if (buffered) buffered.push(message);
      else apply(message);
    } else if (message.type === 'reconnect') {
      // While the snapshot it joined with loads, the page has just rendered
      // or re-synced. One asking again has not: it is told in its turn, among
      // the events held.
      if (!buffered) announceReconnect();
      else if (owed) buffered.push(message);
    } else if (message.type === 'alive') {
      // The answer to a check: a page on screen takes it to ask again for a
      // snapshot it is still owed. The checks of a hidden page count too, so
      // that it asks as soon as it is shown.
      if (owed && !buffered && ++waited >= patience && !document.hidden) loadSnapshot(message.id);
    }
  }

  function expectAnswer(otherwise) {
    if (answerTimer) return;
    answerTimer = setTimeout(otherwise, ANSWER_TIMEOUT_MS);
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

  function goSolo(late) {
    leave();
    sharing = false;
    openOwnStream(late);
  }

  function rejoin() {
    leave();
    join(true);
  }

  // The browser can end a worker without telling its pages (a phone short of
  // memory, a renderer that crashed). Each page asks now and then, hidden
  // ones included since they ring and count unread too, and starts over when
  // nothing answers.
  function check() {
    if (!port) return;
    port.postMessage({ type: 'check', visible: !document.hidden });
    expectAnswer(rejoin);
  }

  function join(resumed) {
    if (port) return;
    returning = resumed;
    let worker;
    try {
      worker = new SharedWorker(scriptUrl, { name: workerName });
    } catch (e) {
      goSolo(resumed);
      return;
    }
    const joined = worker.port;
    port = joined;
    // The script did not load. Only until the worker has spoken: Firefox also
    // reports here what a healthy worker failed to catch.
    worker.onerror = function () { if (port === joined) goSolo(resumed); };
    joined.onmessage = function (e) {
      if (port !== joined) return;
      worker.onerror = null;
      onWorkerMessage(e.data);
    };
    joined.postMessage({
      type: 'hello',
      user: userId,
      cursor: lastEventId,
      returning: resumed,
      visible: !document.hidden,
    });
    // A worker that failed to start says nothing.
    expectAnswer(function () { goSolo(true); });
    checkTimer = setInterval(check, CHECK_INTERVAL_MS);
  }

  // Reconnect SSE when the page becomes visible again (mobile resume)
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) return;
    if (sharing) check();
    else if (stream.isClosed()) stream.reopen();
  });

  // A bfcache restore (mobile back/forward) resumes a frozen page: no script
  // re-runs and no request goes out. A page that shares its stream left it on
  // the way in and joins again. One with a stream of its own reopens it
  // whatever its state: the EventSource can still report OPEN while its
  // connection is dead, and the check above would leave the page on stale
  // data forever. Either way listeners are told to re-sync, once a stream is
  // there to bring what follows.
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
    openOwnStream(false);
  }
})();
