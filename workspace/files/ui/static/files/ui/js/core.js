// --- Folder navigation history ---
// Uses two persistent hidden <a> elements (#folder-nav-push / #folder-nav-replace)
// to navigate via Alpine AJAX, just like clicking a folder link.
window.folderNav = {
  _stack: [],
  _index: -1,
  _skipPush: false,

  init() {
    this._stack = [window.location.pathname + window.location.search];
    this._index = 0;
    window.addEventListener('popstate', () => { this._skipPush = true; });
  },

  // Called from x-init on #folder-browser after every AJAX render
  onNavigate(url) {
    if (this._skipPush) {
      const idx = this._stack.lastIndexOf(url);
      if (idx !== -1) this._index = idx;
      this._skipPush = false;
    } else if (url !== this._stack[this._index]) {
      this._stack = this._stack.slice(0, this._index + 1);
      this._stack.push(url);
      this._index = this._stack.length - 1;
    }
    window.dispatchEvent(new Event('nav-state-changed'));
  },

  canGoBack()    { return this._index > 0; },
  canGoForward() { return this._index < this._stack.length - 1; },

  back() {
    if (!this.canGoBack()) return;
    this._skipPush = true;
    this._index--;
    this._clickNavLink(this._stack[this._index], false);
  },

  forward() {
    if (!this.canGoForward()) return;
    this._skipPush = true;
    this._index++;
    this._clickNavLink(this._stack[this._index], false);
  },

  navigateTo(url) {
    if (url) this._clickNavLink(url, true);
  },

  _clickNavLink(url, push) {
    const link = document.getElementById(push ? 'folder-nav-push' : 'folder-nav-replace');
    if (!link) return;
    link.href = url;
    link.click();
  },
};
window.folderNav.init();

// --- Navigation buttons Alpine component ---
window.navButtons = function navButtons() {
  return {
    canGoBack: false,
    canGoForward: false,
    parentUrl: '',

    init() {
      this._syncState();
      window.addEventListener('nav-state-changed', () => this._syncState());
    },

    _syncState() {
      this.canGoBack = window.folderNav.canGoBack();
      this.canGoForward = window.folderNav.canGoForward();
      this.parentUrl = document.getElementById('folder-browser')?.dataset.parentUrl || '';
    },

    navigateUp() {
      window.folderNav.navigateTo(this.parentUrl);
    },
  };
};

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
