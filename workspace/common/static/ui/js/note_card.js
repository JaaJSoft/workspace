/**
 * Note (file) card popover - hover a graph node or an editor note-link to
 * preview a file's title, tags and first content line.
 *
 * Mirrors the event card popover (event_card.js) but uses a SINGLE shared
 * popover element and accepts either a DOM element or a synthetic
 * { getBoundingClientRect() } anchor - the graph is canvas-drawn, so its nodes
 * are not DOM elements. Reuses window._computePopoverPosition /
 * _applyPopoverTransform / _setPopoverContent from avatar.js.
 */

/* Cache with 30s TTL */
const _noteCardCache = {};
const _noteCardCacheTimes = {};
const _NOTE_CARD_CACHE_TTL = 30000;

/* Loading placeholder shown while a card is being fetched. */
const _NOTE_CARD_LOADING_HTML =
  '<div class="p-4 flex justify-center"><span class="loading loading-spinner loading-sm"></span></div>';

/* Single shared popover state */
let _noteCardPopover = null;
let _noteCardUuid = null;
let _noteCardPlacement = 'bottom';
let _noteCardShowTimeout = null;
let _noteCardHideTimeout = null;
let _noteCardCloseTimeout = null;
let _noteCardFetchingUuid = null;

/**
 * Extract the target file UUID from a note-link href (/notes?file=<uuid>).
 * Pure (no DOM). Returns the canonical uuid string, or null when absent.
 */
function noteCardFileUuidFromHref(href) {
  if (!href) return null;
  const m = /[?&]file=([0-9a-fA-F-]{36})/.exec(href);
  return m ? m[1] : null;
}
window.noteCardFileUuidFromHref = noteCardFileUuidFromHref;

function _noteCardEnsurePopover() {
  if (_noteCardPopover) return _noteCardPopover;
  const popover = document.createElement('div');
  popover.className = 'note-card-popover fixed z-[9999] bg-base-100 rounded-xl shadow-lg ring-1 ring-base-300';
  popover.style.transition = 'opacity 150ms ease-out, transform 150ms ease-out';
  popover.style.opacity = '0';
  popover.style.display = 'none';
  window._setPopoverContent(popover, _NOTE_CARD_LOADING_HTML);
  popover.addEventListener('mouseenter', function() { window._noteCardCancelHide(); });
  popover.addEventListener('mouseleave', function() { window._noteCardScheduleHide(); });
  document.body.appendChild(popover);
  _noteCardPopover = popover;
  return popover;
}

/**
 * Show the popover for `uuid`, anchored to `anchor`, after a 500ms delay.
 * If the popover is already visible for the same uuid, does nothing.
 */
window._noteCardShow = function(anchor, uuid) {
  window._noteCardCancelHide();

  if (_noteCardPopover && _noteCardUuid === uuid &&
      _noteCardPopover.style.display !== 'none' && _noteCardPopover.style.opacity === '1') {
    return;
  }

  if (_noteCardShowTimeout) clearTimeout(_noteCardShowTimeout);

  _noteCardShowTimeout = setTimeout(function() {
    _noteCardShowTimeout = null;
    const popover = _noteCardEnsurePopover();
    _noteCardUuid = uuid;

    const pos = window._computePopoverPosition(anchor, 240);
    popover.style.left = pos.left + 'px';
    popover.style.top = pos.top + 'px';
    _noteCardPlacement = pos.placement;

    popover.style.display = '';
    popover.style.transition = 'none';
    window._applyPopoverTransform(popover, pos.placement, false);
    void popover.offsetHeight;
    popover.style.transition = 'opacity 150ms ease-out, transform 150ms ease-out';
    window._applyPopoverTransform(popover, pos.placement, true);

    const cached = _noteCardCache[uuid];
    const cacheValid = cached !== undefined &&
      (_noteCardCacheTimes[uuid] || 0) + _NOTE_CARD_CACHE_TTL > Date.now();
    if (cacheValid) {
      window._setPopoverContent(popover, cached);
      if (typeof Alpine !== 'undefined') Alpine.initTree(popover);
    } else if (_noteCardFetchingUuid !== uuid) {
      _noteCardFetchingUuid = uuid;
      // Reset to the spinner so the previous note's card isn't shown while loading.
      window._setPopoverContent(popover, _NOTE_CARD_LOADING_HTML);
      fetch('/files/' + uuid + '/card', { credentials: 'same-origin' })
        .then(function(r) { return r.ok ? r.text() : ''; })
        .then(function(html) {
          _noteCardCache[uuid] = html;
          _noteCardCacheTimes[uuid] = Date.now();
          if (_noteCardFetchingUuid === uuid) _noteCardFetchingUuid = null;
          // Inject only if this is still the note being shown.
          if (_noteCardPopover && _noteCardUuid === uuid) {
            window._setPopoverContent(_noteCardPopover, html);
            if (typeof Alpine !== 'undefined') Alpine.initTree(_noteCardPopover);
          }
        })
        .catch(function() { if (_noteCardFetchingUuid === uuid) _noteCardFetchingUuid = null; });
    }
  }, 500);
};

/** Schedule hiding the popover with a 200ms delay. */
window._noteCardScheduleHide = function() {
  if (_noteCardShowTimeout) {
    clearTimeout(_noteCardShowTimeout);
    _noteCardShowTimeout = null;
  }
  if (_noteCardHideTimeout) clearTimeout(_noteCardHideTimeout);
  _noteCardHideTimeout = setTimeout(function() {
    if (_noteCardPopover) {
      window._applyPopoverTransform(_noteCardPopover, _noteCardPlacement, false);
      _noteCardCloseTimeout = setTimeout(function() {
        if (_noteCardPopover) _noteCardPopover.style.display = 'none';
        _noteCardUuid = null;
      }, 150);
    }
  }, 200);
};

/** Cancel a pending hide and restore the visible state. */
window._noteCardCancelHide = function() {
  if (_noteCardHideTimeout) { clearTimeout(_noteCardHideTimeout); _noteCardHideTimeout = null; }
  if (_noteCardCloseTimeout) { clearTimeout(_noteCardCloseTimeout); _noteCardCloseTimeout = null; }
  if (_noteCardPopover && _noteCardPopover.style.display !== 'none') {
    window._applyPopoverTransform(_noteCardPopover, _noteCardPlacement, true);
  }
};
