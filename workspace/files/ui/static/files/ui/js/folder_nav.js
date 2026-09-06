// --- Folder navigation history ---
// A history stack for alpine-ajax navigation. Moving through it means
// clicking one of two persistent hidden <a> elements, #folder-nav-push and
// #folder-nav-replace, that the page renders outside its swap target with
// an x-target pointing at it: the request then goes through alpine-ajax
// exactly like a click on a folder link. The authenticated browser and the
// public share page both use it; each renders its own pair of links.
window.folderNav = {
  _stack: [],
  _index: -1,
  _skipPush: false,

  init() {
    this._stack = [window.location.pathname + window.location.search];
    this._index = 0;
    window.addEventListener('popstate', () => { this._skipPush = true; });
  },

  // Called from x-init on the swap target after every AJAX render
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

  // Re-fetch the current entry without touching the stack.
  reload() {
    if (this._index >= 0) this._clickNavLink(this._stack[this._index], false);
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
// `containerId` names the swap target; its data-parent-url attribute is
// what the Up button follows, so the server decides what "up" means.
window.navButtons = function navButtons(containerId) {
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
      this.parentUrl = document.getElementById(containerId)?.dataset.parentUrl || '';
    },

    navigateUp() {
      window.folderNav.navigateTo(this.parentUrl);
    },
  };
};
