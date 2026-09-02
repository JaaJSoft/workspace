/**
 * Lucide Icons Utilities
 *
 * Helper functions to initialize and refresh Lucide icons in various contexts.
 * These utilities handle common patterns like Alpine.js nextTick and Alpine.js $nextTick.
 */

/**
 * Initialize Lucide icons immediately.
 * Use this when you need to initialize icons synchronously.
 */
function initLucideIcons() {
  if (typeof lucide !== 'undefined' && lucide.createIcons) {
    renderLucideIcons(document);
  }
}

/**
 * Replace every `[data-lucide]` placeholder (and stale svg) under
 * `container` with a fresh svg, without Alpine's MutationObserver watching.
 *
 * To tell a moved node from a removed one, Alpine checks every removed
 * initialised node against every added node in the same mutation batch
 * (`added.some(n => n.contains(removed))`). One render pass over a large
 * listing swaps thousands of `<i>` for as many `<svg>` in a single batch,
 * so that check goes quadratic: a 1,500-entry folder draws 9,000 icons,
 * which is 80 million `contains` calls and a multi-second freeze.
 *
 * The swap therefore runs inside `Alpine.mutateDom`, which hides it from
 * the observer, and the two things the observer would have done are done
 * by hand: release the outgoing nodes' directives (an icon can carry
 * `x-show`), and bind the new svgs, which inherit the placeholder's
 * attributes. Without Alpine on the page there is nothing to hide from.
 *
 * @param {Document|Element} container - where to look for icons
 */
function renderLucideIcons(container) {
  const alpine = typeof Alpine !== 'undefined' && Alpine.mutateDom ? Alpine : null;
  if (!alpine) {
    lucide.createIcons({ root: container });
    return;
  }
  container.querySelectorAll('[data-lucide]').forEach((el) => alpine.destroyTree(el));
  alpine.mutateDom(() => lucide.createIcons({ root: container }));
  container.querySelectorAll('svg[data-lucide]').forEach((svg) => alpine.initTree(svg));
}

/**
 * Initialize Lucide icons after a delay (using setTimeout).
 * Useful for content that loads asynchronously or after DOM updates.
 *
 * @param {number} delay - Delay in milliseconds (default: 100ms)
 */
function initLucideIconsDelayed(delay = 100) {
  setTimeout(() => {
    initLucideIcons();
  }, delay);
}

/**
 * Initialize Lucide icons on the next tick using requestAnimationFrame.
 * Use this when you need to wait for the browser to finish rendering.
 */
function initLucideIconsNextFrame() {
  requestAnimationFrame(() => {
    initLucideIcons();
  });
}

/**
 * Initialize Lucide icons with Alpine.js $nextTick.
 * Use this inside Alpine.js components where you have access to `this.$nextTick`.
 *
 * @param {object} alpineContext - The Alpine.js component context (this)
 */
function initLucideIconsAlpineNextTick(alpineContext) {
  if (alpineContext && alpineContext.$nextTick) {
    alpineContext.$nextTick(() => {
      initLucideIcons();
    });
  } else {
    // Fallback to immediate initialization
    initLucideIcons();
  }
}

/**
 * Initialize Lucide icons after Alpine.js global initialization.
 * Use this for icons that need to be initialized after Alpine has fully processed the DOM.
 */
function initLucideIconsAfterAlpine() {
  if (typeof Alpine !== 'undefined') {
    queueMicrotask(() => {
      initLucideIcons();
    });
  } else {
    // Fallback if Alpine is not available
    initLucideIconsNextFrame();
  }
}

/**
 * Initialize Lucide icons in a specific DOM element.
 * Useful when you only want to initialize icons in a specific container.
 *
 * @param {HTMLElement|string} element - The DOM element or selector
 */
function initLucideIconsInElement(element) {
  if (typeof lucide !== 'undefined' && lucide.createIcons) {
    const container = typeof element === 'string'
      ? document.querySelector(element)
      : element;

    if (container) {
      lucide.createIcons({ nameAttr: 'data-lucide', attrs: {} });
    }
  }
}

/**
 * Observer-based Lucide initialization.
 * Watches for DOM changes and automatically renders Lucide icons: newly
 * added `<i data-lucide>` nodes, and existing icons whose `data-lucide`
 * value changes in place (a reactive Alpine `:data-lucide` binding only
 * rewrites the attribute on the already-drawn svg - without this the drawn
 * paths silently freeze on their initial state).
 *
 * Renders are scoped: each changed element queues its parent container and
 * a debounced render pass (see `renderLucideIcons`) re-renders every
 * `[data-lucide]` element inside - including already-hydrated svgs, which
 * Lucide rebuilds from the current attribute value. (Lucide has no
 * per-node API: an unknown option like `nodes` is ignored and would fall
 * back to a full-document scan.)
 *
 * @param {HTMLElement} root - The root element to observe (default: document.body)
 * @returns {Function} A function to disconnect the observer
 */
function observeLucideIcons(root = document.body) {
  if (typeof lucide === 'undefined' || !lucide.createIcons) {
    return () => {};
  }

  let stale = new Set();
  let pending = null;

  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === 'attributes') {
        const el = mutation.target;
        const name = el.getAttribute('data-lucide');
        // Re-hydration re-sets data-lucide to the value it already has
        // (Lucide copies it onto the created svg, Alpine re-binds it); the
        // old-value comparison is what breaks that re-processing cycle.
        if (name === null || name === mutation.oldValue) continue;
        if (mutation.oldValue && el instanceof SVGElement) {
          // Lucide merges the stale svg's classes into its replacement, so
          // the old icon-name class would pile up across re-renders.
          el.classList.remove(`lucide-${mutation.oldValue}`);
        }
        if (el.parentElement) stale.add(el.parentElement);
        continue;
      }
      for (const node of mutation.addedNodes) {
        if (node.nodeType !== 1) continue;
        // Skip SVG elements — Lucide copies data-lucide onto created <svg>,
        // so without this check we'd re-process them in an infinite loop.
        if (node instanceof SVGElement) continue;
        if (node.hasAttribute?.('data-lucide') || node.querySelector?.('[data-lucide]:not(svg)')) {
          // querySelectorAll(root) never matches the root itself, so an
          // icon placeholder needs its parent as the render container.
          stale.add(node.parentElement ?? node);
        }
      }
    }
    if (!stale.size || pending) return;

    // Debounce: batch multiple rapid mutations into one render pass
    pending = requestAnimationFrame(() => {
      const containers = stale;
      stale = new Set();
      pending = null;
      for (const container of containers) {
        if (container.isConnected) renderLucideIcons(container);
      }
    });
  });

  observer.observe(root, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['data-lucide'],
    attributeOldValue: true,
  });
  return () => observer.disconnect();
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    initLucideIcons,
    initLucideIconsDelayed,
    initLucideIconsNextFrame,
    initLucideIconsAlpineNextTick,
    initLucideIconsAfterAlpine,
    initLucideIconsInElement,
    observeLucideIcons,
    renderLucideIcons
  };
} else {
  window.LucideUtils = {
    init: initLucideIcons,
    delayed: initLucideIconsDelayed,
    nextFrame: initLucideIconsNextFrame,
    alpineNextTick: initLucideIconsAlpineNextTick,
    afterAlpine: initLucideIconsAfterAlpine,
    inElement: initLucideIconsInElement,
    observe: observeLucideIcons,
    render: renderLucideIcons
  };
}
