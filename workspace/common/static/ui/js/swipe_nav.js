// Horizontal swipe navigation for viewer-like components.
// Classic script: exposes window.attachSwipeNavigation.

window.attachSwipeNavigation = function attachSwipeNavigation(el, opts) {
  const threshold = (opts && opts.threshold) || 50;
  let startX = null;
  let startY = null;

  function onTouchStart(e) {
    if (e.touches.length !== 1) {
      startX = null;
      return;
    }
    startX = e.touches[0].clientX;
    startY = e.touches[0].clientY;
  }

  function onTouchEnd(e) {
    if (startX === null) return;
    const t = e.changedTouches && e.changedTouches[0];
    if (!t) return;
    const dx = t.clientX - startX;
    const dy = t.clientY - startY;
    startX = null;
    startY = null;
    // Ignore short swipes and vertical-dominant gestures (scrolling).
    if (Math.abs(dx) < threshold || Math.abs(dx) <= Math.abs(dy)) return;
    if (dx < 0) {
      opts.onNext();
    } else {
      opts.onPrev();
    }
  }

  function onTouchCancel() {
    startX = null;
    startY = null;
  }

  el.addEventListener('touchstart', onTouchStart, { passive: true });
  el.addEventListener('touchend', onTouchEnd);
  el.addEventListener('touchcancel', onTouchCancel);

  return function detach() {
    el.removeEventListener('touchstart', onTouchStart);
    el.removeEventListener('touchend', onTouchEnd);
    el.removeEventListener('touchcancel', onTouchCancel);
  };
};
