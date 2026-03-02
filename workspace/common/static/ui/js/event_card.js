/**
 * Event card popover — hover to preview event details.
 * Follows the same pattern as user card popover in avatar.js.
 * Reuses window._computePopoverPosition, _applyPopoverTransform, _setPopoverContent.
 */

/* ── Cache with 30s TTL ───────────────────────────────────────── */
var _eventCardCache = {};
var _eventCardCacheTimes = {};
var _EVENT_CARD_CACHE_TTL = 30000;

/**
 * Show the event card popover for a wrapper element.
 * 500ms delay before showing to avoid accidental triggers.
 */
window._eventCardShow = function(wrapper, eventId) {
  window._eventCardCancelHide(wrapper);

  var existing = wrapper._eventCardPopover;
  if (existing && existing.style.display !== 'none' && existing.style.opacity === '1') {
    return;
  }

  if (wrapper._showTimeout) clearTimeout(wrapper._showTimeout);

  // For recurring virtual occurrences, the ID is "masterUuid:isoDate" — extract the master UUID and occurrence start
  var fetchId = eventId;
  var occStart = '';
  var colonIdx = String(eventId).indexOf(':');
  if (colonIdx > 0) {
    fetchId = eventId.substring(0, colonIdx);
    occStart = eventId.substring(colonIdx + 1);
  }

  wrapper._showTimeout = setTimeout(function() {
    wrapper._showTimeout = null;

    var popover = wrapper._eventCardPopover;
    if (!popover) {
      popover = document.createElement('div');
      popover.className = 'event-card-popover fixed z-[9999] bg-base-100 rounded-xl shadow-lg ring-1 ring-base-300';
      popover.style.transition = 'opacity 150ms ease-out, transform 150ms ease-out';
      popover.style.opacity = '0';
      var spinWrap = document.createElement('div');
      spinWrap.className = 'p-4 flex justify-center';
      var spinner = document.createElement('span');
      spinner.className = 'loading loading-spinner loading-sm';
      spinWrap.appendChild(spinner);
      popover.appendChild(spinWrap);
      popover.addEventListener('mouseenter', function() { window._eventCardCancelHide(wrapper); });
      popover.addEventListener('mouseleave', function() { window._eventCardScheduleHide(wrapper); });
      document.body.appendChild(popover);
      wrapper._eventCardPopover = popover;
    }

    var pos = window._computePopoverPosition(wrapper, 240);
    popover.style.left = pos.left + 'px';
    popover.style.top = pos.top + 'px';
    wrapper._placement = pos.placement;

    popover.style.display = '';
    popover.style.transition = 'none';
    window._applyPopoverTransform(popover, pos.placement, false);
    void popover.offsetHeight;
    popover.style.transition = 'opacity 150ms ease-out, transform 150ms ease-out';
    window._applyPopoverTransform(popover, pos.placement, true);

    var cacheKey = occStart ? fetchId + ':' + occStart : fetchId;
    var cached = _eventCardCache[cacheKey];
    var cacheValid = cached && (_eventCardCacheTimes[cacheKey] || 0) + _EVENT_CARD_CACHE_TTL > Date.now();
    if (cacheValid) {
      window._setPopoverContent(popover, cached);
      lucide?.createIcons({ nodes: popover.querySelectorAll('[data-lucide]') });
      _formatEventCardTimes(popover);
      if (typeof Alpine !== 'undefined') Alpine.initTree(popover);
    } else if (!wrapper._fetching) {
      wrapper._fetching = true;
      var cardUrl = '/calendar/events/' + fetchId + '/card';
      if (occStart) cardUrl += '?start=' + encodeURIComponent(occStart);
      fetch(cardUrl, { credentials: 'same-origin' })
        .then(function(r) { return r.ok ? r.text() : ''; })
        .then(function(html) {
          _eventCardCache[cacheKey] = html;
          _eventCardCacheTimes[cacheKey] = Date.now();
          wrapper._fetching = false;
          if (wrapper._eventCardPopover) {
            window._setPopoverContent(wrapper._eventCardPopover, html);
            lucide?.createIcons({ nodes: wrapper._eventCardPopover.querySelectorAll('[data-lucide]') });
            _formatEventCardTimes(wrapper._eventCardPopover);
            if (typeof Alpine !== 'undefined') Alpine.initTree(wrapper._eventCardPopover);
          }
        })
        .catch(function() { wrapper._fetching = false; });
    }
  }, 500);
};

/**
 * Schedule hiding the event card popover with a 200ms delay.
 */
window._eventCardScheduleHide = function(wrapper) {
  if (wrapper._showTimeout) {
    clearTimeout(wrapper._showTimeout);
    wrapper._showTimeout = null;
  }

  wrapper._hideTimeout = setTimeout(function() {
    var popover = wrapper._eventCardPopover;
    if (popover) {
      window._applyPopoverTransform(popover, wrapper._placement || 'bottom', false);
      wrapper._closeTimeout = setTimeout(function() { popover.style.display = 'none'; }, 150);
    }
  }, 200);
};

/**
 * Cancel a pending hide for the event card popover.
 */
window._eventCardCancelHide = function(wrapper) {
  if (wrapper._hideTimeout) {
    clearTimeout(wrapper._hideTimeout);
    wrapper._hideTimeout = null;
  }
  if (wrapper._closeTimeout) {
    clearTimeout(wrapper._closeTimeout);
    wrapper._closeTimeout = null;
  }
  var popover = wrapper._eventCardPopover;
  if (popover && popover.style.display !== 'none') {
    window._applyPopoverTransform(popover, wrapper._placement || 'bottom', true);
  }
};

/**
 * Format <time data-localtime> elements inside a freshly injected popover.
 * Reuses the same logic as the global localtime formatter in base.html.
 */
function _formatEventCardTimes(container) {
  container.querySelectorAll('time[data-localtime]').forEach(function(el) {
    var d = new Date(el.getAttribute('datetime'));
    if (isNaN(d)) return;
    var mode = el.dataset.localtime;
    if (mode === 'time') {
      el.textContent = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } else if (mode === 'date') {
      el.textContent = d.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
    } else {
      el.textContent = d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
  });
}
