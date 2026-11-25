/**
 * Hover popover primitives: placement next to a trigger, the slide+fade
 * transition, and filling the popover with a server-rendered partial.
 * Every hover card (users, events, tasks, notes, mail contacts) builds on them.
 */

/**
 * Compute fixed position for a popover relative to a trigger element.
 * @param {HTMLElement} trigger
 * @param {number} [minSpace=280] - minimum space needed below trigger
 * @returns {{ top: number, left: number, placement: string }}
 */
window._computePopoverPosition = function _computePopoverPosition(trigger, minSpace) {
  if (minSpace === undefined) minSpace = 280;
  const rect = trigger.getBoundingClientRect();
  let centerX = rect.left + rect.width / 2;
  const spaceBelow = window.innerHeight - rect.bottom;
  const placement = spaceBelow < minSpace ? 'top' : 'bottom';
  const top = placement === 'top' ? rect.top - 8 : rect.bottom + 8;

  // Clamp horizontally so the popover (w-64 = 256px) stays in viewport
  const halfWidth = 128;
  const margin = 8;
  centerX = Math.max(halfWidth + margin, Math.min(centerX, window.innerWidth - halfWidth - margin));

  return { top: top, left: centerX, placement: placement };
};

/**
 * Apply the slide+fade transform to a popover element.
 * @param {HTMLElement} popover
 * @param {string} placement - 'top' or 'bottom'
 * @param {boolean} visible - true = final state, false = initial (hidden) state
 */
window._applyPopoverTransform = function _applyPopoverTransform(popover, placement, visible) {
  const baseX = 'translateX(-50%)';
  const anchorY = placement === 'top' ? ' translateY(-100%)' : '';
  if (visible) {
    popover.style.opacity = '1';
    popover.style.transform = baseX + anchorY;
  } else {
    const slideOffset = placement === 'top' ? ' translateY(calc(-100% + 6px))' : ' translateY(-6px)';
    popover.style.opacity = '0';
    popover.style.transform = baseX + slideOffset;
  }
};

/**
 * Set server-rendered HTML content on popover element.
 * Content comes from our own Django view (trusted server-rendered HTML).
 * @param {HTMLElement} el
 * @param {string} html - trusted server-rendered HTML
 */
window._setPopoverContent = function _setPopoverContent(el, html) {
  el.textContent = '';
  const tpl = document.createElement('template');
  tpl.innerHTML = html;  // safe: content is from our own server endpoint
  el.appendChild(tpl.content);
};
