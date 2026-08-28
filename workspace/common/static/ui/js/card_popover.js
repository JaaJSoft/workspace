/**
 * Hover card popover — fetches a server-rendered partial and floats it next
 * to the hovered element. Generic over the partial's URL: events and project
 * tasks both ride on it, and anything else with a card endpoint can too.
 * Follows the same pattern as user card popover in avatar.js.
 * Reuses window._computePopoverPosition, _applyPopoverTransform, _setPopoverContent.
 */

/* ── Cache with 30s TTL ───────────────────────────────────────── */
const _cardPopoverCache = {};
const _cardPopoverCacheTimes = {};
const _CARD_POPOVER_CACHE_TTL = 30000;

/**
 * Show a card popover for a wrapper element, filled from `url`.
 * 500ms delay before showing to avoid accidental triggers.
 * `cacheKey` defaults to the URL; pass one only when several URLs render
 * the same card.
 */
window._cardPopoverShow = function(wrapper, url, cacheKey) {
  window._cardPopoverCancelHide(wrapper);

  const existing = wrapper._cardPopover;
  if (existing && existing.style.display !== 'none' && existing.style.opacity === '1') {
    return;
  }

  if (wrapper._showTimeout) clearTimeout(wrapper._showTimeout);

  const key = cacheKey || url;

  wrapper._showTimeout = setTimeout(function() {
    wrapper._showTimeout = null;

    let popover = wrapper._cardPopover;
    if (!popover) {
      popover = document.createElement('div');
      popover.className = 'event-card-popover fixed z-[9999] bg-base-100 rounded-xl shadow-lg ring-1 ring-base-300';
      popover.style.transition = 'opacity 150ms ease-out, transform 150ms ease-out';
      popover.style.opacity = '0';
      const spinWrap = document.createElement('div');
      spinWrap.className = 'p-4 flex justify-center';
      const spinner = document.createElement('span');
      spinner.className = 'loading loading-spinner loading-sm';
      spinWrap.appendChild(spinner);
      popover.appendChild(spinWrap);
      popover.addEventListener('mouseenter', function() { window._cardPopoverCancelHide(wrapper); });
      popover.addEventListener('mouseleave', function() { window._cardPopoverScheduleHide(wrapper); });
      document.body.appendChild(popover);
      wrapper._cardPopover = popover;
    }

    const pos = window._computePopoverPosition(wrapper, 240);
    popover.style.left = pos.left + 'px';
    popover.style.top = pos.top + 'px';
    wrapper._placement = pos.placement;

    popover.style.display = '';
    popover.style.transition = 'none';
    window._applyPopoverTransform(popover, pos.placement, false);
    void popover.offsetHeight;
    popover.style.transition = 'opacity 150ms ease-out, transform 150ms ease-out';
    window._applyPopoverTransform(popover, pos.placement, true);

    const cached = _cardPopoverCache[key];
    const cacheValid = cached && (_cardPopoverCacheTimes[key] || 0) + _CARD_POPOVER_CACHE_TTL > Date.now();
    if (cacheValid) {
      window._setPopoverContent(popover, cached);
      _formatCardTimes(popover);
      if (typeof Alpine !== 'undefined') Alpine.initTree(popover);
    } else if (!wrapper._fetching) {
      wrapper._fetching = true;
      fetch(url, { credentials: 'same-origin' })
        .then(function(r) { return r.ok ? r.text() : ''; })
        .then(function(html) {
          _cardPopoverCache[key] = html;
          _cardPopoverCacheTimes[key] = Date.now();
          wrapper._fetching = false;
          if (wrapper._cardPopover) {
            window._setPopoverContent(wrapper._cardPopover, html);
            _formatCardTimes(wrapper._cardPopover);
            if (typeof Alpine !== 'undefined') Alpine.initTree(wrapper._cardPopover);
          }
        })
        .catch(function() { wrapper._fetching = false; });
    }
  }, 500);
};

/**
 * Schedule hiding the card popover with a 200ms delay.
 */
window._cardPopoverScheduleHide = function(wrapper) {
  if (wrapper._showTimeout) {
    clearTimeout(wrapper._showTimeout);
    wrapper._showTimeout = null;
  }

  wrapper._hideTimeout = setTimeout(function() {
    const popover = wrapper._cardPopover;
    if (popover) {
      window._applyPopoverTransform(popover, wrapper._placement || 'bottom', false);
      wrapper._closeTimeout = setTimeout(function() { popover.style.display = 'none'; }, 150);
    }
  }, 200);
};

/**
 * Cancel a pending hide for the card popover.
 */
window._cardPopoverCancelHide = function(wrapper) {
  if (wrapper._hideTimeout) {
    clearTimeout(wrapper._hideTimeout);
    wrapper._hideTimeout = null;
  }
  if (wrapper._closeTimeout) {
    clearTimeout(wrapper._closeTimeout);
    wrapper._closeTimeout = null;
  }
  const popover = wrapper._cardPopover;
  if (popover && popover.style.display !== 'none') {
    window._applyPopoverTransform(popover, wrapper._placement || 'bottom', true);
  }
};

/**
 * Format <time data-localtime> elements inside a freshly injected popover.
 * Reuses the same logic as the global localtime formatter in base.html.
 */
function _formatCardTimes(container) {
  const tz = window.getUserTimeZone ? window.getUserTimeZone() : undefined;
  container.querySelectorAll('time[data-localtime]').forEach(function(el) {
    const d = new Date(el.getAttribute('datetime'));
    if (isNaN(d)) return;
    const mode = el.dataset.localtime;
    if (mode === 'time') {
      el.textContent = d.toLocaleTimeString([], { timeZone: tz, hour: '2-digit', minute: '2-digit' });
    } else if (mode === 'date') {
      el.textContent = d.toLocaleDateString([], { timeZone: tz, month: 'short', day: 'numeric', year: 'numeric' });
    } else {
      el.textContent = d.toLocaleDateString([], { timeZone: tz, month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString([], { timeZone: tz, hour: '2-digit', minute: '2-digit' });
    }
  });
}
