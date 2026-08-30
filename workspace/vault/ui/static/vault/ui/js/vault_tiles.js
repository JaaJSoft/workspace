// The geometry of a tile grid, shared by the two vault screens.
//
// The five steps of the slider, as the file browser draws them: a minimum
// column width, the gap between tiles, and the icon inside. The two screens
// keep their own chosen step - a vault card and an entry card are not looked
// at the same way - but the steps themselves are one list.
window.vaultTiles = {
  STEPS: {
    1: { width: 110, gap: 8, icon: 28 },
    2: { width: 140, gap: 10, icon: 36 },
    3: { width: 180, gap: 12, icon: 44 },
    4: { width: 230, gap: 14, icon: 56 },
    5: { width: 290, gap: 16, icon: 72 },
  },

  // Anything outside the five steps is refused rather than clamped: it can
  // only come from a stored preference someone edited by hand, and the
  // caller's current size is a better answer than an invented one.
  isStep: function (size) {
    return Object.prototype.hasOwnProperty.call(this.STEPS, size);
  },

  width: function (size) {
    return (this.STEPS[size] || this.STEPS[3]).width;
  },

  gap: function (size) {
    return (this.STEPS[size] || this.STEPS[3]).gap;
  },

  icon: function (size) {
    return (this.STEPS[size] || this.STEPS[3]).icon;
  },
};
