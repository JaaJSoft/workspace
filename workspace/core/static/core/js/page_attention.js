// Whether someone is actually in front of this page: the tab is visible, the
// window has focus, and there was keyboard or pointer input recently.
//
// An open tab keeps making requests on its own (SSE-driven refreshes), and
// the presence middleware counts every request as activity. A tab left open
// on an idle PC would then look active forever and hold back web push on the
// user's other devices. Same-origin requests sent while the page is
// unattended carry UNATTENDED_HEADER, which the middleware does not count.
//
// window.pageAttention.isAttended() answers the question; a `page:attended`
// window event fires when the user comes back (tab shown, window focused,
// first input after being idle).
(function () {
  const IDLE_AFTER_MS = 2 * 60 * 1000;
  const UNATTENDED_HEADER = 'X-Page-Unattended';
  const INPUT_EVENTS = ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart'];

  let lastInputAt = Date.now();

  function isAttended() {
    return document.visibilityState === 'visible'
      && document.hasFocus()
      && Date.now() - lastInputAt < IDLE_AFTER_MS;
  }

  function announceIfAttended() {
    if (isAttended()) window.dispatchEvent(new CustomEvent('page:attended'));
  }

  function noteInput() {
    const now = Date.now();
    // Bound to pointermove: one update per second is plenty.
    if (now - lastInputAt < 1000) return;
    const wasAttended = isAttended();
    lastInputAt = now;
    if (!wasAttended) announceIfAttended();
  }

  for (const type of INPUT_EVENTS) {
    window.addEventListener(type, noteInput, { passive: true });
  }
  document.addEventListener('visibilitychange', announceIfAttended);
  window.addEventListener('focus', announceIfAttended);

  function isSameOrigin(input) {
    const url = typeof input === 'object' && input !== null && 'url' in input ? input.url : String(input);
    try {
      return new URL(url, window.location.href).origin === window.location.origin;
    } catch (e) {
      return false;
    }
  }

  // Cross-origin requests are left alone: a custom header would turn them
  // into CORS preflights the other server may refuse.
  const nativeFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    if (isAttended() || !isSameOrigin(input)) return nativeFetch(input, init);
    // Headers from init replace the Request's own, as fetch() does itself.
    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
    headers.set(UNATTENDED_HEADER, '1');
    return nativeFetch(input, { ...init, headers });
  };

  window.pageAttention = { isAttended, UNATTENDED_HEADER };
})();
