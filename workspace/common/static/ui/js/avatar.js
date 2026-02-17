/**
 * Generate avatar HTML for a user.
 * Attempts to load the avatar image; falls back to initials on error.
 *
 * @param {number|string} userId
 * @param {string} username
 * @param {string} sizeClass - Tailwind size classes, e.g. 'w-7 h-7 text-xs'
 * @returns {string} HTML string
 */
window.userAvatarHtml = function(userId, username, sizeClass) {
  const initial = (username || '?')[0].toUpperCase();
  const imgUrl = `/api/v1/users/${userId}/avatar`;

  return `<div class="avatar relative" data-user-id="${userId}">` +
    `<div class="${sizeClass} rounded-full overflow-hidden" ` +
      `:class="'ring-2 ring-offset-base-100 ring-offset-1 ' + $store.presence.ringClass(${userId})">` +
      `<img src="${imgUrl}" alt="${username}" class="block w-full h-full object-cover" ` +
        `onerror="this.onerror=null;` +
        `var d=this.closest('.avatar');` +
        `d.className='avatar placeholder relative';` +
        `d.firstElementChild.className='${sizeClass} bg-neutral text-neutral-content rounded-full flex items-center justify-center';` +
        `this.replaceWith(Object.assign(document.createElement('span'),{textContent:'${initial}'}));" />` +
    `</div>` +
    `<span class="absolute bottom-0 right-0 block w-2.5 h-2.5 rounded-full ring-2 ring-base-100" :class="$store.presence.dotClass(${userId})"></span>` +
  `</div>`;
};

/* ── User card popover (global cache with 30s TTL) ────────────── */
const _userCardCache = {};
const _userCardCacheTimes = {};
const _USER_CARD_CACHE_TTL = 30000; // 30 seconds

/**
 * Compute fixed position for a popover relative to a trigger element.
 * @param {HTMLElement} trigger
 * @returns {{ top: number, left: number, placement: string }}
 */
function _computePopoverPosition(trigger) {
  var rect = trigger.getBoundingClientRect();
  var centerX = rect.left + rect.width / 2;
  var spaceBelow = window.innerHeight - rect.bottom;
  var placement = spaceBelow < 280 ? 'top' : 'bottom';
  var top = placement === 'top' ? rect.top - 8 : rect.bottom + 8;

  // Clamp horizontally so the popover (w-64 = 256px) stays in viewport
  var halfWidth = 128;
  var margin = 8;
  centerX = Math.max(halfWidth + margin, Math.min(centerX, window.innerWidth - halfWidth - margin));

  return { top: top, left: centerX, placement: placement };
}

/**
 * Apply the slide+fade transform to a popover element.
 * @param {HTMLElement} popover
 * @param {string} placement - 'top' or 'bottom'
 * @param {boolean} visible - true = final state, false = initial (hidden) state
 */
function _applyPopoverTransform(popover, placement, visible) {
  var baseX = 'translateX(-50%)';
  var anchorY = placement === 'top' ? ' translateY(-100%)' : '';
  if (visible) {
    popover.style.opacity = '1';
    popover.style.transform = baseX + anchorY;
  } else {
    var slideOffset = placement === 'top' ? ' translateY(calc(-100% + 6px))' : ' translateY(-6px)';
    popover.style.opacity = '0';
    popover.style.transform = baseX + slideOffset;
  }
}

/**
 * Generate avatar HTML wrapped in a popover that shows a user card on hover.
 * Uses imperative DOM events (no Alpine x-data) so it works when injected via x-html.
 *
 * @param {number|string} userId
 * @param {string} username
 * @param {string} sizeClass - Tailwind size classes, e.g. 'w-7 h-7 text-xs'
 * @returns {string} HTML string
 */
window.userAvatarWithCardHtml = function(userId, username, sizeClass) {
  const avatar = window.userAvatarHtml(userId, username, sizeClass);
  return `<div class="flex" onmouseenter="window._userCardShow(this,${userId})" onmouseleave="window._userCardScheduleHide(this)">` +
    avatar +
  `</div>`;
};

/**
 * Set server-rendered HTML content on popover element.
 * Content comes from our own Django view (trusted server-rendered HTML).
 * @param {HTMLElement} el
 * @param {string} html - trusted server-rendered HTML
 */
function _setPopoverContent(el, html) {
  el.textContent = '';
  var tpl = document.createElement('template');
  tpl.innerHTML = html;  // safe: content is from our own server endpoint
  el.appendChild(tpl.content);
}

/**
 * Show the user card popover for a wrapper element.
 * Waits 1 second before showing to avoid accidental triggers.
 * The popover is appended to document.body with position:fixed to avoid overflow clipping.
 */
window._userCardShow = function(wrapper, userId) {
  window._userCardCancelHide(wrapper);

  // If popover is already visible, nothing to do
  var existing = wrapper._userCardPopover;
  if (existing && existing.style.display !== 'none' && existing.style.opacity === '1') {
    return;
  }

  // Cancel any existing show timeout (re-entry)
  if (wrapper._showTimeout) clearTimeout(wrapper._showTimeout);

  // Delay show by 1 second
  wrapper._showTimeout = setTimeout(function() {
    wrapper._showTimeout = null;

    // Create popover element if not yet present
    var popover = wrapper._userCardPopover;
    if (!popover) {
      popover = document.createElement('div');
      popover.className = 'user-card-popover fixed z-[9999] bg-base-100 rounded-xl shadow-lg ring-1 ring-base-300';
      popover.style.transition = 'opacity 150ms ease-out, transform 150ms ease-out';
      popover.style.opacity = '0';
      var spinWrap = document.createElement('div');
      spinWrap.className = 'p-4 flex justify-center';
      var spinner = document.createElement('span');
      spinner.className = 'loading loading-spinner loading-sm';
      spinWrap.appendChild(spinner);
      popover.appendChild(spinWrap);
      popover.addEventListener('mouseenter', function() { window._userCardCancelHide(wrapper); });
      popover.addEventListener('mouseleave', function() { window._userCardScheduleHide(wrapper); });
      document.body.appendChild(popover);
      wrapper._userCardPopover = popover;
    }

    // Position using fixed coordinates
    var pos = _computePopoverPosition(wrapper);
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
    var cached = _userCardCache[userId];
    var cacheValid = cached && (_userCardCacheTimes[userId] || 0) + _USER_CARD_CACHE_TTL > Date.now();
    if (cacheValid) {
      _setPopoverContent(popover, cached);
      lucide?.createIcons({ nodes: popover.querySelectorAll('[data-lucide]') });
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
            lucide?.createIcons({ nodes: wrapper._userCardPopover.querySelectorAll('[data-lucide]') });
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
  // Cancel pending show if user leaves before the 1s delay
  if (wrapper._showTimeout) {
    clearTimeout(wrapper._showTimeout);
    wrapper._showTimeout = null;
  }

  wrapper._hideTimeout = setTimeout(function() {
    var popover = wrapper._userCardPopover;
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
  var popover = wrapper._userCardPopover;
  if (popover && popover.style.display !== 'none') {
    _applyPopoverTransform(popover, wrapper._placement || 'bottom', true);
  }
};

/* ── Alpine component for server-rendered avatars (show_card=True) ── */

/**
 * Alpine.js component for user card popover.
 * Used by _user_avatar_inner.html when show_card=True.
 * Delegates to the imperative _userCardShow/_userCardScheduleHide functions
 * which create and manage their own popover on document.body.
 *
 * @param {number|string} userId
 * @returns {object} Alpine data object
 */
window.userCard = function(userId) {
  return {
    show() {
      window._userCardShow(this.$el, userId);
    },
    scheduleHide() {
      window._userCardScheduleHide(this.$el);
    },
    destroy() {
      // Clean up the imperative popover when Alpine removes this component
      var popover = this.$el._userCardPopover;
      if (popover && popover.parentNode) {
        popover.parentNode.removeChild(popover);
      }
    }
  };
};
