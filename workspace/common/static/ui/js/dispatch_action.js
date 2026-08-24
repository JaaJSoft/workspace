/**
 * Buttons whose only job is to broadcast an event declare it in the markup as
 * `data-dispatch="<event-name>"`; one delegated listener turns that into a
 * window event. Replaces the inline `onclick` handlers that a
 * Content-Security-Policy without 'unsafe-inline' refuses to run.
 */
document.addEventListener('click', function(event) {
  const target = event.target;
  if (!(target instanceof Element)) return;
  const trigger = target.closest('[data-dispatch]');
  if (!trigger) return;
  window.dispatchEvent(new CustomEvent(trigger.dataset.dispatch));
});
