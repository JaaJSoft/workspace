// Global Server-Sent Events stream + module registry.
//
// Reads the modules registry from <script id="workspace-modules-data" type="application/json">
// (rendered via Django's |json_script filter). For each active module the
// registry holds {slug, name, icon, color} so notification cards can
// reference it by slug.
//
// Re-dispatches each SSE payload as a window CustomEvent named "sse:<event>",
// plus dash-separated aliases ("chat-<event>", "ai-stream-<event>") for
// events whose name starts with "chat." or "ai_stream." — Alpine's
// @event.window syntax parses dots as modifiers, so templates need a
// dot-free event name to bind to.
//
// One tab per browser holds the stream. The tabs of a user form a group: the
// one holding the Web Lock (the leader) opens the stream and posts every event
// on a BroadcastChannel, the others (followers) dispatch what they receive.
// The lock's queue is the election: when the leader goes, the browser hands
// the lock to the next tab, which reopens the stream where the group stopped.
// Listeners cannot tell the two roles apart.
(function () {
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

  const STREAM_URL = '/api/v1/stream';
  const SNAPSHOT_URL = '/api/v1/stream/snapshot';
  // How often a visible follower checks that the leader still answers, and how
  // long it waits for the answer before taking the stream over.
  const PROBE_INTERVAL_MS = 15000;
  const PROBE_TIMEOUT_MS = 3000;

  let es = null;
  let errorCount = 0;
  let retryTimer = null;
  // Resume point for the next connect(). EventSource replays the last id on
  // its own reconnects, but every stream we open ourselves (visibility
  // change, bfcache restore, a follower taking over) is a brand new
  // EventSource with no id to replay - so we carry it in the URL and the
  // server reads it from there. A follower keeps it up to date from what the
  // leader relays.
  let lastEventId = '';
  // Skip the first connect's reconnect event — initial state comes from the
  // server-rendered template, not from a missed SSE push, so listeners that
  // re-fetch on reconnect would cause an unnecessary round-trip on page load.
  let firstConnect = true;

  // Web Locks only exist in a secure context: over plain HTTP outside
  // localhost every tab keeps a stream of its own.
  const locks = window.navigator && window.navigator.locks;
  const userId = document.body && document.body.dataset.userId;
  let sharing = Boolean(locks && window.BroadcastChannel && userId);
  // Named after the user: a tab still showing another account's page must
  // neither lead nor follow this one's.
  const groupName = 'workspace-sse:' + userId;
  const tabId = Math.random().toString(36).slice(2);

  let channel = null;
  let leading = false;
  // The tab whose relays this one trusts: the last to announce it took the lock.
  let leaderId = null;
  // Bumped on every join and leave, so a callback from an earlier stay notices.
  let membership = 0;
  // Counts the times this tab took the lock, so the loss of an earlier hold
  // is not mistaken for the loss of the current one.
  let tenure = 0;
  // AbortController of the lock request waiting in the queue.
  let waiting = null;
  // Resolvers of the promises holding the lock.
  let releases = [];
  // Relays held back while the snapshot loads; null once it has.
  let buffered = null;
  let probeTimer = null;
  let pongTimer = null;

  function dispatch(payload) {
    window.dispatchEvent(new CustomEvent('sse:' + payload.event, { detail: payload.data }));
    if (payload.event.startsWith('chat.')) {
      window.dispatchEvent(new CustomEvent('chat-' + payload.event.substring(5), { detail: payload.data }));
    }
    if (payload.event.startsWith('ai_stream.')) {
      window.dispatchEvent(new CustomEvent('ai-stream-' + payload.event.substring(10), { detail: payload.data }));
    }
  }

  // The stream was reopened, or this tab changed streams: pushes may have been
  // missed meanwhile - let listeners re-sync.
  function announceReconnect() {
    window.dispatchEvent(new CustomEvent('sse:reconnect'));
  }

  function relay(message) {
    if (leading && channel) channel.postMessage({ ...message, from: tabId });
  }

  function onMessage(e) {
    if (e.lastEventId) lastEventId = e.lastEventId;
    const payload = JSON.parse(e.data);
    dispatch(payload);
    relay({ type: 'event', id: lastEventId, payload });
  }

  function disconnect() {
    if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
    if (es) { es.close(); es = null; }
  }

  function connect() {
    disconnect();
    const url = lastEventId
      ? STREAM_URL + '?last_event_id=' + encodeURIComponent(lastEventId)
      : STREAM_URL;
    es = new EventSource(url);
    errorCount = 0;
    es.addEventListener('sse', onMessage);
    es.onerror = function () {
      if (++errorCount > 5) { es.close(); es = null; retryTimer = setTimeout(connect, 30000); }
    };
    es.onopen = function () {
      errorCount = 0;
      if (!firstConnect) {
        announceReconnect();
        relay({ type: 'reconnect' });
      }
      firstConnect = false;
    };
  }

  function apply(message) {
    if (message.type === 'event') dispatch(message.payload);
    else if (message.type === 'reconnect') announceReconnect();
  }

  function onGroupMessage(message) {
    if (!message) return;
    if (message.type === 'ping') {
      if (leading) relay({ type: 'pong', id: lastEventId });
      return;
    }
    // Everything else comes from a leader. A leader that stopped answering and
    // was replaced still relays what it had in hand once it wakes up: only the
    // last tab to announce itself is listened to.
    if (message.type === 'leader') leaderId = message.from;
    if (leading || (leaderId && message.from !== leaderId)) return;
    // Whatever it says, the leader is alive.
    clearTimeout(pongTimer);
    pongTimer = null;
    if (message.id) lastEventId = message.id;
    if (message.type !== 'event' && message.type !== 'reconnect') return;
    if (buffered) buffered.push(message);
    else apply(message);
  }

  // A tab that follows opened no stream, so it never got the events a stream
  // starts with (unread counts, generations under way): it asks the server.
  // Relays wait for the answer and are applied after it, the order a stream
  // would have delivered them in.
  function loadSnapshot() {
    const held = [];
    buffered = held;
    fetch(SNAPSHOT_URL, { credentials: 'same-origin' })
      .then((response) => (response.ok ? response.json() : null))
      .catch(() => null)
      .then((snapshot) => {
        // Promoted or gone meanwhile: the buffer was dealt with there.
        if (buffered !== held) return;
        buffered = null;
        const events = snapshot && Array.isArray(snapshot.events) ? snapshot.events : [];
        for (const payload of events) dispatch(payload);
        for (const message of held) apply(message);
      });
  }

  function probe() {
    if (leading || !channel || pongTimer || document.hidden) return;
    const mine = membership;
    channel.postMessage({ type: 'ping' });
    pongTimer = setTimeout(function () {
      pongTimer = null;
      if (mine === membership && !leading) takeOver(mine);
    }, PROBE_TIMEOUT_MS);
  }

  function stopProbing() {
    clearInterval(probeTimer);
    clearTimeout(pongTimer);
    probeTimer = null;
    pongTimer = null;
  }

  // A leader the browser suspended without a word (a background tab on a
  // phone), or one whose page is stuck, keeps the lock and relays nothing.
  // Only a visible tab checks: nobody is looking at the others.
  function startProbing() {
    stopProbing();
    probeTimer = setInterval(probe, PROBE_INTERVAL_MS);
    probe();
  }

  function lead(resync) {
    leading = true;
    // A no-op for the request that was just granted, and the end of any other.
    if (waiting) { waiting.abort(); waiting = null; }
    stopProbing();
    relay({ type: 'leader' });
    // What the previous leader relayed before it went is still news.
    const held = buffered;
    buffered = null;
    if (held) held.forEach(apply);
    firstConnect = !resync;
    connect();
  }

  function follow(mine, resync) {
    leading = false;
    if (resync) announceReconnect();
    loadSnapshot();
    waiting = new AbortController();
    contend({ signal: waiting.signal }, mine, true);
    startProbing();
  }

  // Another tab took the lock from this one, which had stopped answering.
  function standDown(mine) {
    releases = [];
    disconnect();
    follow(mine, true);
  }

  function takeOver(mine) {
    // Or the queued request would be granted on top of the stolen lock. It may
    // have been granted already, the leader going at that very moment: the
    // steal then breaks this tab's own hold, and contend() takes both in.
    if (waiting) { waiting.abort(); waiting = null; }
    contend({ steal: true }, mine, true);
  }

  // The lock API refused the request outright: back to a stream of its own.
  function goSolo() {
    leave();
    sharing = false;
    connect();
  }

  function contend(options, mine, resync) {
    let held = 0;
    locks.request(groupName, options, function (lock) {
      if (mine !== membership) return undefined;
      if (!lock) {
        // ifAvailable, and another tab leads.
        follow(mine, resync);
        return undefined;
      }
      held = ++tenure;
      if (!leading) lead(resync);
      // The lock is held for as long as this promise is pending.
      return new Promise(function (resolve) { releases.push(resolve); });
    }).catch(function (error) {
      if (mine !== membership) return;
      if (held) {
        if (held === tenure) standDown(mine);
      } else if (error.name !== 'AbortError') {
        goSolo();
      }
    });
  }

  function join(resumed) {
    if (channel) return;
    const mine = ++membership;
    leaderId = null;
    channel = new BroadcastChannel(groupName);
    channel.onmessage = function (e) { onGroupMessage(e.data); };
    // Alone, a tab leads at once; next to a leader it learns right away that
    // it follows, and what it has to ask the server for.
    contend({ ifAvailable: true }, mine, resumed);
  }

  function leave() {
    membership += 1;
    stopProbing();
    buffered = null;
    if (waiting) { waiting.abort(); waiting = null; }
    leading = false;
    disconnect();
    for (const letGo of releases) letGo();
    releases = [];
    if (channel) { channel.close(); channel = null; }
  }

  // Reconnect SSE when the page becomes visible again (mobile resume)
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) return;
    if (sharing && !leading) { probe(); return; }
    if (!es || es.readyState === EventSource.CLOSED) connect();
  });

  // A bfcache restore (mobile back/forward) resumes a frozen page: no script
  // re-runs, no request goes out, and the EventSource can still report OPEN
  // while its underlying connection is already dead - so the readyState check
  // above would skip the reconnect and leave the page on stale data forever.
  // Reconnect unconditionally instead; connect() closes any previous stream,
  // and onopen then fires sse:reconnect so listeners re-sync.
  window.addEventListener('pageshow', function (e) {
    if (!e.persisted) return;
    if (sharing) join(true);
    else connect();
  });

  if (sharing) {
    // A page the browser sets aside (back/forward cache, frozen tab) runs no
    // script: left holding the lock, it would starve every other tab. It
    // leaves the group on the way out and joins again when it comes back.
    window.addEventListener('pagehide', function () { if (sharing) leave(); });
    document.addEventListener('freeze', function () { if (sharing) leave(); });
    document.addEventListener('resume', function () { if (sharing) join(true); });
    join(false);
  } else {
    connect();
  }
})();
