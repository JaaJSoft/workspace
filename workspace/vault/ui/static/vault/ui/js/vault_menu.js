// Where a context menu ends up: the entry rows, the folders, the listing's
// empty space and the switcher all place one this way.
//
// The cursor is where the menu starts, not where it has to stay: rows carry
// their trigger in the last column, against the right edge, and a panel left
// at the click hangs off the page where nothing can read or click it.
window.vaultMenu = {
  MARGIN: 8,

  // Pure, so the arithmetic is testable without a layout: the caller passes
  // the panel's measured size and the viewport it has to fit in.
  clamp: function (x, y, width, height, viewport) {
    const margin = this.MARGIN;
    return {
      x: Math.max(margin, Math.min(x, viewport.width - width - margin)),
      y: Math.max(margin, Math.min(y, viewport.height - height - margin)),
    };
  },

  // Measured rather than guessed: a menu is as tall as the registry made it.
  // Runs after the panel has been shown, hence the tick - a hidden element
  // measures zero.
  fit: function (component, id, key) {
    if (typeof document === 'undefined' || !component.$nextTick) return;
    const self = this;
    component.$nextTick(function () {
      const panel = document.getElementById(id);
      const state = component[key];
      if (!panel || !state || !state.open) return;
      const rect = panel.getBoundingClientRect();
      component[key] = Object.assign(
        {},
        state,
        self.clamp(state.x, state.y, rect.width, rect.height, {
          width: window.innerWidth,
          height: window.innerHeight,
        })
      );
    });
  },
};
