// The one owner of browser history. Loaded in <head>, before Alpine: its
// popstate listener has to run ahead of alpine-ajax's.
//
// Every entry carries `__nav`:
//   doc    the page load that wrote it
//   gen    which swapped content it shows: alpine-ajax records a swap with
//          state {__ajax: true}, and each one is a new generation
//   depth  how many in-app entries lie behind it, fwd how far ahead they go
//   prev   the address it was pushed from
// so a back or forward knows whether the page on screen still matches the
// entry it lands on, and whether there is an in-app page to go back to.
//
// Modules never call history.* nor listen to popstate. They write with
// navHistory.push / replace / returnTo and listen to `nav:restore`, fired
// on window after each back or forward:
//   detail.state  what the module stored with the entry
//   detail.stale  the content on screen belongs to another entry: refresh
//                 it and call preventDefault(), or the page reloads
(function () {
  const rawPush = history.pushState.bind(history);
  const rawReplace = history.replaceState.bind(history);
  const doc = Math.random().toString(36).slice(2);
  let generations = 0;
  let shownGen = 0;
  // The __nav of the entry on screen. history.state is already the next
  // entry's by the time popstate fires.
  let current;

  function navOf(state) {
    return state && typeof state === 'object' ? state.__nav : undefined;
  }

  function own(state) {
    if (!state || typeof state !== 'object') return {};
    const { __nav, __ajax, ...rest } = state;
    return rest;
  }

  function here() {
    return location.pathname + location.search;
  }

  function address(url) {
    const parsed = new URL(url, location.href);
    return parsed.pathname + parsed.search;
  }

  function write(raw, state, nav, url) {
    current = nav;
    raw({ ...(state && typeof state === 'object' ? state : {}), __nav: nav }, '', url);
    window.dispatchEvent(new CustomEvent('nav:change'));
  }

  function freshGen(state) {
    if (state && state.__ajax) shownGen = ++generations;
    return shownGen;
  }

  // Wrapped rather than left alone: alpine-ajax calls these itself.
  history.pushState = function (state, _title, url) {
    const depth = current.depth + 1;
    const gen = freshGen(state);
    write(rawPush, state, { doc, gen, depth, fwd: depth, prev: here() }, url);
  };

  history.replaceState = function (state, _title, url) {
    const gen = freshGen(state);
    write(rawReplace, state, { ...current, doc, gen }, url);
  };

  // A page reached by a link from another page of the app has that page
  // behind it; one opened from outside, or in a new tab, has nothing.
  function cameFrom() {
    if (history.length < 2 || !document.referrer) return null;
    const referrer = new URL(document.referrer);
    return referrer.origin === location.origin ? referrer.pathname + referrer.search : null;
  }

  // A reload keeps the entry's place in the history, not its content.
  const landed = navOf(history.state);
  const from = landed ? null : cameFrom();
  current = landed
    ? { ...landed, doc, gen: 0 }
    : { doc, gen: 0, depth: from ? 1 : 0, fwd: from ? 1 : 0, prev: from };
  rawReplace({ ...own(history.state), __nav: current }, '');

  // A same-document link to a #fragment adds an entry nobody tagged.
  window.addEventListener('hashchange', () => {
    if (navOf(history.state)) return;
    const depth = current.depth + 1;
    current = { doc, gen: shownGen, depth, fwd: depth, prev: current.prev };
    rawReplace({ __nav: current }, '');
  });

  window.addEventListener('popstate', (event) => {
    const nav = navOf(event.state);
    if (!nav) return;
    event.stopImmediatePropagation();
    const fwd = nav.depth < current.depth ? Math.max(current.depth, current.fwd) : nav.fwd;
    const stale = nav.doc !== doc || nav.gen !== shownGen;
    current = { ...nav, fwd };
    const restore = new CustomEvent('nav:restore', {
      cancelable: true,
      detail: { state: own(event.state), stale },
    });
    window.dispatchEvent(restore);
    if (stale) {
      if (!restore.defaultPrevented) {
        rawReplace({ ...event.state, __nav: current }, '');
        location.reload();
        return;
      }
      shownGen = ++generations;
      current = { ...current, doc, gen: shownGen };
    }
    rawReplace({ ...event.state, __nav: current }, '');
    window.dispatchEvent(new CustomEvent('nav:change'));
  });

  // <a href="fallback" data-nav-back>: back to the previous in-app page, or
  // the link's own target when there is none (a page opened from outside).
  // Capture phase, so it decides before alpine-ajax sees the click.
  document.addEventListener(
    'click',
    (event) => {
      const link = event.target.closest && event.target.closest('[data-nav-back]');
      if (!link || event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (!window.navHistory.canGoBack()) return;
      event.preventDefault();
      history.back();
    },
    true,
  );

  window.navHistory = {
    push(url, state) {
      history.pushState(state, '', url);
    },

    replace(url, state) {
      history.replaceState(state, '', url);
    },

    // What the module stored with the entry on screen.
    state() {
      return own(history.state);
    },

    canGoBack() {
      return current.depth > 0;
    },

    canGoForward() {
      return current.fwd > current.depth;
    },

    back() {
      history.back();
    },

    forward() {
      history.forward();
    },

    // The page now shows `url` (a panel closed, a detail left for its list).
    // Steps back onto it when it is the entry this one was pushed from, so
    // closing undoes the opening; otherwise rewrites this entry and returns
    // false, and the caller has already updated what is on screen.
    returnTo(url, state) {
      if (current.depth > 0 && current.prev === address(url)) {
        history.back();
        return true;
      }
      history.replaceState(state, '', url);
      return false;
    },
  };
})();
