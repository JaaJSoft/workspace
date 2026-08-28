
/**
 * User card popover — the card that floats next to an avatar on hover, plus
 * the positioning helpers the event and note card popovers share with it.
 *
 * The avatar itself is the <user-avatar> element (ui/js/user_avatar.js); it
 * calls _userCardShow/_userCardScheduleHide from here when given the `card`
 * attribute.
 */

/* ── Patch user card status from Alpine presence store ────────── */

const _statusConfig = {
  bot:     { dot: 'bg-secondary', label: 'text-secondary', showAgo: false },
  online:  { dot: 'bg-success',  label: 'text-success',  showAgo: false },
  away:    { dot: 'bg-warning',  label: 'text-warning',  showAgo: true  },
  busy:    { dot: 'bg-error',    label: 'text-error',    showAgo: false },
  offline: { dot: 'bg-base-300', label: 'text-base-content/40', showAgo: true  },
};

function _patchCardStatus(container) {
  const el = container.querySelector('[data-user-card-status]');
  if (!el) return;
  const userId = parseInt(el.dataset.userId, 10);
  if (!userId || typeof Alpine === 'undefined') return;

  const status = Alpine.store('presence').statusOf(userId);
  const cfg = _statusConfig[status] || _statusConfig.offline;

  const dot = el.querySelector('[data-status-dot]');
  const label = el.querySelector('[data-status-label]');
  const ago = el.querySelector('[data-status-ago]');

  if (dot) {
    dot.className = 'w-2 h-2 rounded-full flex-shrink-0 ' + cfg.dot;
  }
  if (label) {
    label.className = 'font-medium capitalize ' + cfg.label;
    label.textContent = status;
  }
  if (ago) {
    const lastSeen = el.dataset.lastSeen;
    ago.textContent = cfg.showAgo && lastSeen ? window.formatLastSeenAgo(lastSeen) : '';
  }
}

/* ── User card popover (global cache with 30s TTL) ────────────── */
const _userCardCache = {};
const _userCardCacheTimes = {};
const _USER_CARD_CACHE_TTL = 30000; // 30 seconds

/**
 * Compute fixed position for a popover relative to a trigger element.
 * Shared by user card and event card popovers.
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
 * Shared by user card and event card popovers.
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

/**
 * Show the user card popover for a wrapper element.
 * Waits 500ms before showing to avoid accidental triggers.
 * The popover is appended to document.body with position:fixed to avoid overflow clipping.
 */
window._userCardShow = function(wrapper, userId) {
  window._userCardCancelHide(wrapper);

  const existing = wrapper._userCardPopover;
  if (existing && existing.style.display !== 'none' && existing.style.opacity === '1') {
    return;
  }

  // Cancel any existing show timeout (re-entry)
  if (wrapper._showTimeout) clearTimeout(wrapper._showTimeout);

  wrapper._showTimeout = setTimeout(function() {
    wrapper._showTimeout = null;

    let popover = wrapper._userCardPopover;
    if (!popover) {
      popover = document.createElement('div');
      popover.className = 'user-card-popover fixed z-[9999] bg-base-100 rounded-xl shadow-lg ring-1 ring-base-300';
      popover.style.transition = 'opacity 150ms ease-out, transform 150ms ease-out';
      popover.style.opacity = '0';
      const spinWrap = document.createElement('div');
      spinWrap.className = 'p-4 flex justify-center';
      const spinner = document.createElement('span');
      spinner.className = 'loading loading-spinner loading-sm';
      spinWrap.appendChild(spinner);
      popover.appendChild(spinWrap);
      popover.addEventListener('mouseenter', function() { window._userCardCancelHide(wrapper); });
      popover.addEventListener('mouseleave', function() { window._userCardScheduleHide(wrapper); });
      document.body.appendChild(popover);
      wrapper._userCardPopover = popover;
    }

    const pos = _computePopoverPosition(wrapper);
    popover.style.left = pos.left + 'px';
    popover.style.top = pos.top + 'px';
    wrapper._placement = pos.placement;

    // Slide+fade in — set initial state without transition, then animate
    popover.style.display = '';
    popover.style.transition = 'none';
    _applyPopoverTransform(popover, pos.placement, false);
    void popover.offsetHeight;
    popover.style.transition = 'opacity 150ms ease-out, transform 150ms ease-out';
    _applyPopoverTransform(popover, pos.placement, true);

    // Fetch content if not cached (or cache expired)
    const cached = _userCardCache[userId];
    const cacheValid = cached && (_userCardCacheTimes[userId] || 0) + _USER_CARD_CACHE_TTL > Date.now();
    if (cacheValid) {
      _setPopoverContent(popover, cached);
      _patchCardStatus(popover);
    } else if (!wrapper._fetching) {
      wrapper._fetching = true;
      fetch('/users/' + userId + '/card', { credentials: 'same-origin' })
        .then(function(r) { return r.ok ? r.text() : ''; })
        .then(function(html) {
          _userCardCache[userId] = html;
          _userCardCacheTimes[userId] = Date.now();
          wrapper._fetching = false;
          if (wrapper._userCardPopover) {
            _setPopoverContent(wrapper._userCardPopover, html);
            _patchCardStatus(wrapper._userCardPopover);
          }
        })
        .catch(function() { wrapper._fetching = false; });
    }
  }, 500);
};

/**
 * Schedule hiding the user card popover with a 200ms delay.
 * Also cancels any pending show timeout.
 */
window._userCardScheduleHide = function(wrapper) {
  // Cancel pending show if user leaves before the 500ms delay
  if (wrapper._showTimeout) {
    clearTimeout(wrapper._showTimeout);
    wrapper._showTimeout = null;
  }

  wrapper._hideTimeout = setTimeout(function() {
    const popover = wrapper._userCardPopover;
    if (popover) {
      _applyPopoverTransform(popover, wrapper._placement || 'bottom', false);
      wrapper._closeTimeout = setTimeout(function() { popover.style.display = 'none'; }, 150);
    }
  }, 200);
};

/**
 * Cancel a pending hide for the user card popover.
 */
window._userCardCancelHide = function(wrapper) {
  if (wrapper._hideTimeout) {
    clearTimeout(wrapper._hideTimeout);
    wrapper._hideTimeout = null;
  }
  if (wrapper._closeTimeout) {
    clearTimeout(wrapper._closeTimeout);
    wrapper._closeTimeout = null;
  }
  // Restore visible state if popover was fading out
  const popover = wrapper._userCardPopover;
  if (popover && popover.style.display !== 'none') {
    _applyPopoverTransform(popover, wrapper._placement || 'bottom', true);
  }
};

