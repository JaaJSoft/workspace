// How the vault is being looked at, and where that survives a reload.
//
// Two settings - list or tiles, and how big a tile is - kept on this device
// rather than on the account: they describe a screen, not a preference the
// server has any business holding.
//
// A mixin rather than part of the controller, which is long enough already.
// The state itself stays where the component declares it (in vaultStore):
// what lives here is reading it back, writing it, and the geometry the tile
// size drives.
//
// Methods, never getters: object spread copies values, so a `get` here would
// be evaluated once at composition and frozen.
window.vaultViewPrefsMixin = function vaultViewPrefsMixin() {
  const VIEW_MODE_KEY = 'vault.browser.viewMode';
  const TILE_SIZE_KEY = 'vault.browser.tileSize';

  function readPreference(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (err) {
      // Private browsing and a blocked-storage setting both throw on read. A
      // screen that forgets how it was being looked at is a smaller loss than
      // one that does not mount.
      return null;
    }
  }

  function writePreference(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (err) {
      /* nothing to do: the preference does not survive the reload */
    }
  }

  return {
    // Called from the component's own init(), which is what knows whether the
    // state it restores into comes from the component or from the store.
    restoreViewPrefs: function () {
      const viewMode = readPreference(VIEW_MODE_KEY);
      if (viewMode) this.viewMode = viewMode;
      const tileSize = Number(readPreference(TILE_SIZE_KEY));
      if (window.vaultTiles.isStep(tileSize)) this.tileSize = tileSize;
    },

    setViewMode: function (mode) {
      this.viewMode = mode;
      writePreference(VIEW_MODE_KEY, mode);
    },

    setTileSize: function (size) {
      const step = Number(size);
      // Off the scale is a bug or a stale preference, and a tile of zero
      // pixels is not a smaller tile.
      if (!window.vaultTiles.isStep(step)) return;
      this.tileSize = step;
      writePreference(TILE_SIZE_KEY, String(step));
    },

    tileMinWidth: function () {
      return window.vaultTiles.width(this.tileSize);
    },

    tileGap: function () {
      return window.vaultTiles.gap(this.tileSize);
    },

    tileIconSize: function () {
      return window.vaultTiles.icon(this.tileSize);
    },
  };
};
