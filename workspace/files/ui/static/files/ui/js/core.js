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
