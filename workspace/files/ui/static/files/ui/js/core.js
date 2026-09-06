// --- Action loading state ---
// Global function + Alpine.reactive backing so it works regardless of
// nested x-data proxy resolution (Chrome V8 bug with Proxy + with()).
// Alpine.reactive() is initialised in fileBrowser().init().
window._actionLoadingState = null;
window.isActionLoading = function (uuid) {
  return !!window._actionLoadingState?.[uuid];
};

// Global clipboard for cut/copy/paste operations
window.fileClipboard = {
  items: [],  // Array of {uuid, name, nodeType}
  mode: null, // 'cut' or 'copy'

  cut(items) {
    this.items = items;
    this.mode = 'cut';
    window.dispatchEvent(new CustomEvent('clipboard-changed'));
  },

  copy(items) {
    this.items = items;
    this.mode = 'copy';
    window.dispatchEvent(new CustomEvent('clipboard-changed'));
  },

  clear() {
    this.items = [];
    this.mode = null;
    window.dispatchEvent(new CustomEvent('clipboard-changed'));
  },

  hasItems() {
    return this.items.length > 0;
  },

  getItems() {
    return this.items;
  },

  getMode() {
    return this.mode;
  },

  isCut() {
    return this.mode === 'cut';
  },

  isCopy() {
    return this.mode === 'copy';
  }
};
