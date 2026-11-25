/**
 * Hover cards: a server-rendered partial that floats next to the hovered
 * element after a short delay. Every card (users, events, tasks, notes, mail
 * contacts) is an instance of this engine; its module supplies the URL and
 * whatever is specific to its card. Built on the primitives of popover.js.
 *
 *   const card = createHoverCard({ className: 'user-card-popover' });
 *   el.addEventListener('mouseenter', () => card.show(el, '/users/1/card'));
 *   el.addEventListener('mouseleave', () => card.scheduleHide(el));
 *
 * Options:
 * - className: added to the popover, for CSS and for tests to find it
 * - minSpace: room needed below the anchor before the card opens above it
 * - loadingClass: added to the loading placeholder (a width, so the card
 *   does not grow sideways past the edge it was placed against)
 * - shared: one popover for the whole instance instead of one per anchor;
 *   the anchor may then be any { getBoundingClientRect() }, a canvas point
 * - onRender(popover): runs each time a card is put in the popover
 * - onClick(event, anchor): click on the card
 *
 * A card is cached by URL for 30s; refresh(anchor) drops the cache and
 * reloads the card that anchor shows.
 */

const _HOVER_CARD_SHOW_DELAY = 500;
const _HOVER_CARD_HIDE_DELAY = 200;
const _HOVER_CARD_FADE = 150;
const _HOVER_CARD_CACHE_TTL = 30000;
const _HOVER_CARD_TRANSITION = 'opacity 150ms ease-out, transform 150ms ease-out';

window.createHoverCard = function createHoverCard(options) {
  const opts = options || {};
  const cache = new Map();
  const inflight = new Map();
  const anchorStates = new WeakMap();
  const sharedState = opts.shared ? {} : null;

  function stateOf(anchor, create) {
    if (sharedState) return sharedState;
    let state = anchorStates.get(anchor);
    if (!state && create) {
      state = {};
      anchorStates.set(anchor, state);
    }
    return state;
  }

  function isOpen(state) {
    const popover = state.popover;
    return !!popover && popover.style.display !== 'none' && popover.style.opacity === '1';
  }

  function clearTimers(state, names) {
    names.forEach(function(name) {
      if (state[name]) {
        clearTimeout(state[name]);
        state[name] = null;
      }
    });
  }

  function showLoading(popover) {
    popover.textContent = '';
    const wrap = document.createElement('div');
    wrap.className = 'p-4 flex justify-center' + (opts.loadingClass ? ' ' + opts.loadingClass : '');
    const spinner = document.createElement('span');
    spinner.className = 'loading loading-spinner loading-sm';
    wrap.appendChild(spinner);
    popover.appendChild(wrap);
  }

  function fill(popover, html) {
    window._setPopoverContent(popover, html);
    if (typeof Alpine !== 'undefined') Alpine.initTree(popover);
    if (opts.onRender) opts.onRender(popover);
  }

  function ensurePopover(state, anchor) {
    if (state.popover) return state.popover;
    const popover = document.createElement('div');
    popover.className = 'fixed z-9999 bg-base-100 rounded-xl shadow-lg ring-1 ring-base-300' +
      (opts.className ? ' ' + opts.className : '');
    popover.style.transition = _HOVER_CARD_TRANSITION;
    popover.style.opacity = '0';
    popover.addEventListener('mouseenter', function() { cancelHide(anchor); });
    popover.addEventListener('mouseleave', function() { scheduleHide(anchor); });
    if (opts.onClick) popover.addEventListener('click', function(e) { opts.onClick(e, anchor); });
    document.body.appendChild(popover);
    state.popover = popover;
    return popover;
  }

  /** The card's HTML: from the cache, or one fetch shared by every caller. */
  function load(url) {
    const cached = cache.get(url);
    if (cached && cached.at + _HOVER_CARD_CACHE_TTL > Date.now()) return Promise.resolve(cached.html);
    if (!inflight.has(url)) {
      inflight.set(url, fetch(url, { credentials: 'same-origin' })
        .then(function(r) { return r.ok ? r.text() : ''; })
        .catch(function() { return ''; })
        .then(function(html) {
          inflight.delete(url);
          // A failed or empty card is not cached, so the next hover retries.
          if (html) cache.set(url, { html: html, at: Date.now() });
          return html;
        }));
    }
    return inflight.get(url);
  }

  function render(state, url) {
    state.url = url;
    const cached = cache.get(url);
    if (cached && cached.at + _HOVER_CARD_CACHE_TTL > Date.now()) {
      fill(state.popover, cached.html);
      return Promise.resolve();
    }
    showLoading(state.popover);
    return load(url).then(function(html) {
      // The pointer may have moved on to another card, or the anchor gone.
      if (state.popover && state.url === url) fill(state.popover, html);
    });
  }

  function show(anchor, url) {
    const state = stateOf(anchor, true);
    cancelHide(anchor);
    if (isOpen(state) && state.url === url) return;
    clearTimers(state, ['showTimer']);

    state.showTimer = setTimeout(function() {
      state.showTimer = null;
      const popover = ensurePopover(state, anchor);
      const pos = window._computePopoverPosition(anchor, opts.minSpace);
      popover.style.left = pos.left + 'px';
      popover.style.top = pos.top + 'px';
      state.placement = pos.placement;

      // Slide+fade in: set the initial state without a transition, then animate.
      popover.style.display = '';
      popover.style.transition = 'none';
      window._applyPopoverTransform(popover, pos.placement, false);
      void popover.offsetHeight;
      popover.style.transition = _HOVER_CARD_TRANSITION;
      window._applyPopoverTransform(popover, pos.placement, true);

      render(state, url);
    }, _HOVER_CARD_SHOW_DELAY);
  }

  function scheduleHide(anchor) {
    const state = stateOf(anchor, false);
    if (!state) return;
    clearTimers(state, ['showTimer', 'hideTimer', 'closeTimer']);
    state.hideTimer = setTimeout(function() {
      state.hideTimer = null;
      const popover = state.popover;
      if (!popover) return;
      window._applyPopoverTransform(popover, state.placement || 'bottom', false);
      state.closeTimer = setTimeout(function() {
        state.closeTimer = null;
        popover.style.display = 'none';
        state.url = null;
      }, _HOVER_CARD_FADE);
    }, _HOVER_CARD_HIDE_DELAY);
  }

  function cancelHide(anchor) {
    const state = stateOf(anchor, false);
    if (!state) return;
    clearTimers(state, ['hideTimer', 'closeTimer']);
    if (state.popover && state.popover.style.display !== 'none') {
      window._applyPopoverTransform(state.popover, state.placement || 'bottom', true);
    }
  }

  /** Tear down the anchor's card: its timers, and the popover left on <body>. */
  function detach(anchor) {
    const state = stateOf(anchor, false);
    if (!state) return;
    clearTimers(state, ['showTimer', 'hideTimer', 'closeTimer']);
    const popover = state.popover;
    if (popover && popover.parentNode) popover.parentNode.removeChild(popover);
    state.popover = null;
    state.url = null;
    if (!sharedState) anchorStates.delete(anchor);
  }

  /** Forget every cached card and reload the one the anchor shows. */
  function refresh(anchor) {
    cache.clear();
    const state = stateOf(anchor, false);
    if (!state || !state.popover || !state.url) return Promise.resolve();
    const url = state.url;
    return load(url).then(function(html) {
      if (state.popover && state.url === url) fill(state.popover, html);
    });
  }

  return { show: show, scheduleHide: scheduleHide, cancelHide: cancelHide, detach: detach, refresh: refresh };
};
