// Global Server-Sent Events stream + module registry.
//
// Reads the modules registry from <script id="workspace-modules-data" type="application/json">
// (rendered via Django's |json_script filter). For each active module the
// registry holds {slug, name, icon, color} so notification cards can
// reference it by slug.
//
// Re-dispatches each SSE payload as a window CustomEvent named "sse:<event>",
// plus a chat-specific alias "chat-<event>" for events whose name starts
// with "chat.".
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

  let es = null;
  let errorCount = 0;
  let retryTimer = null;
  // Skip the first connect's reconnect event — initial state comes from the
  // server-rendered template, not from a missed SSE push, so listeners that
  // re-fetch on reconnect would cause an unnecessary round-trip on page load.
  let firstConnect = true;

  function onMessage(e) {
    const payload = JSON.parse(e.data);
    window.dispatchEvent(new CustomEvent('sse:' + payload.event, { detail: payload.data }));
    if (payload.event.startsWith('chat.')) {
      window.dispatchEvent(new CustomEvent('chat-' + payload.event.substring(5), { detail: payload.data }));
    }
  }

  function connect() {
    if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
    if (es) { es.close(); es = null; }
    es = new EventSource('/api/v1/stream');
    errorCount = 0;
    es.addEventListener('sse', onMessage);
    es.onerror = function () {
      if (++errorCount > 5) { es.close(); es = null; retryTimer = setTimeout(connect, 30000); }
    };
    es.onopen = function () {
      errorCount = 0;
      if (!firstConnect) {
        // Any reopened connection (timer or visibility-resume) may have missed
        // pushes while the stream was down — let listeners re-sync.
        window.dispatchEvent(new CustomEvent('sse:reconnect'));
      }
      firstConnect = false;
    };
  }
  connect();

  // Reconnect SSE when the page becomes visible again (mobile resume)
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden && (!es || es.readyState === EventSource.CLOSED)) {
      connect();
    }
  });
})();
