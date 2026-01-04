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
    lucide.createIcons();
  }
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
    // Use queueMicrotask for the next microtask
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
 * Watches for DOM changes and automatically initializes new Lucide icons.
 * Returns a function to stop observing.
 *
 * @param {HTMLElement} root - The root element to observe (default: document.body)
 * @returns {Function} A function to disconnect the observer
 */
function observeLucideIcons(root = document.body) {
  if (typeof lucide === 'undefined' || !lucide.createIcons) {
    return () => {}; // Return no-op function
  }

  const observer = new MutationObserver((mutations) => {
    let shouldUpdate = false;

    for (const mutation of mutations) {
      if (mutation.type === 'childList' && mutation.addedNodes.length > 0) {
        // Check if any added nodes have data-lucide attribute
        for (const node of mutation.addedNodes) {
          if (node.nodeType === 1) { // Element node
            if (node.hasAttribute?.('data-lucide') ||
                node.querySelector?.('[data-lucide]')) {
              shouldUpdate = true;
              break;
            }
          }
        }
      }
      if (shouldUpdate) break;
    }

    if (shouldUpdate) {
      initLucideIconsNextFrame();
    }
  });

  observer.observe(root, {
    childList: true,
    subtree: true
  });

  // Return disconnect function
  return () => observer.disconnect();
}

// Export for use in modules or make globally available
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    initLucideIcons,
    initLucideIconsDelayed,
    initLucideIconsNextFrame,
    initLucideIconsAlpineNextTick,
    initLucideIconsAfterAlpine,
    initLucideIconsInElement,
    observeLucideIcons
  };
} else {
  // Make functions globally available
  window.LucideUtils = {
    init: initLucideIcons,
    delayed: initLucideIconsDelayed,
    nextFrame: initLucideIconsNextFrame,
    alpineNextTick: initLucideIconsAlpineNextTick,
    afterAlpine: initLucideIconsAfterAlpine,
    inElement: initLucideIconsInElement,
    observe: observeLucideIcons
  };
}
