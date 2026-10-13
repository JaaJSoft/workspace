// --- Folder navigation ---
// Moving between listings means clicking one of two persistent hidden <a>
// elements, #folder-nav-push and #folder-nav-replace, that the page renders
// outside its swap target with an x-target pointing at it: the request then
// goes through alpine-ajax exactly like a click on a folder link. Back and
// forward are the browser's own (navHistory); a step onto another listing
// swaps it in place through the replace link. The authenticated browser and
// the public share page both use it; each renders its own pair of links.
window.folderNav = {
  init() {
    window.addEventListener('nav:restore', (event) => {
      if (!event.detail.stale || !document.getElementById('folder-nav-replace')) return;
      event.preventDefault();
      this.reload();
    });
    window.addEventListener('nav:change', () => this.onNavigate());
  },

  // Called from x-init on the swap target after every AJAX render.
  onNavigate() {
    window.dispatchEvent(new Event('nav-state-changed'));
  },

  canGoBack() { return window.navHistory.canGoBack(); },
  canGoForward() { return window.navHistory.canGoForward(); },
  back() { window.navHistory.back(); },
  forward() { window.navHistory.forward(); },

  navigateTo(url) {
    if (url) this._clickNavLink(url, true);
  },

  // Re-fetch the listing the address bar shows.
  reload() {
    this._clickNavLink(window.location.pathname + window.location.search, false);
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
