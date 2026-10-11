/**
 * Lucide Icons Utilities
 *
 * Draws the icons present at page load (`initLucideIcons`), then keeps
 * drawing the ones added later (`observeLucideIcons`), each render scoped to
 * the container that changed. Content Alpine inserts into the document needs
 * no call of its own: the observer sees it. `renderLucideIcons(container)` is
 * for the rest - icons the observer cannot see, such as a detached subtree.
 * Never draw the whole document after load: `lucide.createIcons()` with no
 * root redraws every icon of the page, already drawn svgs included.
 */

/**
 * Draw every icon of the document. For the first draw of a page only: past
 * that point it redraws icons that are already drawn.
 */
function initLucideIcons() {
  if (typeof lucide !== 'undefined' && lucide.icons) {
    renderLucideIcons(document);
  }
}

// One drawn svg per icon name, built on first use and cloned for every
// occurrence after it. undefined: not looked up yet; null: no such icon.
const lucideTemplates = new Map();

function lucideTemplate(name) {
  let template = lucideTemplates.get(name);
  if (template === undefined) {
    // Lucide's own name mapping: "-", "_" and whitespace start a new word.
    let camel = '';
    let upperNext = false;
    for (const ch of name) {
      if (ch === '-' || ch === '_' || ch <= ' ') {
        upperNext = camel.length > 0;
        continue;
      }
      camel += camel.length === 0 ? ch.toLowerCase() : (upperNext ? ch.toUpperCase() : ch);
      upperNext = false;
    }
    const iconNode = lucide.icons[camel.charAt(0).toUpperCase() + camel.slice(1)];
    template = iconNode ? lucide.createElement(iconNode) : null;
    lucideTemplates.set(name, template);
  }
  return template;
}

/**
 * Replace every `[data-lucide]` element under `root` (placeholder or
 * already drawn svg) with the svg lucide.createIcons would draw: same
 * attributes, in the same order, same classes. The svg is cloned from a
 * per-icon template instead of rebuilt node by node, which is what a
 * listing repeating the same few icons a thousand times pays for.
 *
 * @param {Document|Element} root - where to look for icons
 * @returns {SVGElement[]} the drawn svgs carrying Alpine directives
 */
function drawLucideIcons(root) {
  const bound = [];
  for (const el of root.querySelectorAll('[data-lucide]')) {
    const name = el.getAttribute('data-lucide');
    const template = lucideTemplate(name);
    if (!template) {
      console.warn(`${el.outerHTML} icon name was not found in the provided icons object.`);
      continue;
    }
    const svg = template.cloneNode(true);
    svg.setAttribute('data-lucide', name);
    const attrs = Array.from(el.attributes);
    const a11y = attrs.some((a) => a.name.startsWith('aria-') || a.name === 'role' || a.name === 'title');
    if (!a11y) svg.setAttribute('aria-hidden', 'true');
    const classes = ['lucide', `lucide-${name}`, ...(el.getAttribute('class') || '').split(' ')]
      .filter((c, i, all) => c && c.trim() !== '' && all.indexOf(c) === i)
      .join(' ')
      .trim();
    let directives = false;
    for (const attr of attrs) {
      svg.setAttribute(attr.name, attr.name === 'class' ? classes : attr.value);
      if (/^(x-|:|@)/.test(attr.name)) directives = true;
    }
    if (!el.hasAttribute('class')) svg.setAttribute('class', classes);
    el.parentNode?.replaceChild(svg, el);
    if (directives) bound.push(svg);
  }
  return bound;
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
 * `x-show`), and bind the new svgs that inherited one from their
 * placeholder - binding the plain ones too cost as much as drawing them.
 * Without Alpine on the page there is nothing to hide from.
 *
 * @param {Document|Element} container - where to look for icons
 */
function renderLucideIcons(container) {
  const alpine = typeof Alpine !== 'undefined' && Alpine.mutateDom ? Alpine : null;
  if (!alpine) {
    drawLucideIcons(container);
    return;
  }
  container.querySelectorAll('[data-lucide]').forEach((el) => alpine.destroyTree(el));
  let bound = [];
  alpine.mutateDom(() => {
    bound = drawLucideIcons(container);
  });
  bound.forEach((svg) => alpine.initTree(svg));
}

function hasAncestorIn(el, elements) {
  for (let parent = el.parentElement; parent; parent = parent.parentElement) {
    if (elements.has(parent)) return true;
  }
  return false;
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
 * `[data-lucide]` element inside - including already-hydrated svgs, redrawn
 * from the current attribute value. (Lucide's own createIcons has no
 * per-node API: an unknown option like `nodes` is ignored and falls back to
 * a full-document scan.)
 *
 * @param {HTMLElement} root - The root element to observe (default: document.body)
 * @returns {Function} A function to disconnect the observer
 */
function observeLucideIcons(root = document.body) {
  if (typeof lucide === 'undefined' || !lucide.icons) {
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
        // A container inside another one of the batch is drawn with it.
        // Drawing it again redraws every icon it holds: a swapped listing
        // whose rows the table then re-sorts queues both the listing and
        // its tbody.
        if (container.isConnected && !hasAncestorIn(container, containers)) {
          renderLucideIcons(container);
        }
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
    observeLucideIcons,
    renderLucideIcons,
    drawLucideIcons
  };
} else {
  window.LucideUtils = {
    init: initLucideIcons,
    observe: observeLucideIcons,
    render: renderLucideIcons,
    draw: drawLucideIcons
  };
}
