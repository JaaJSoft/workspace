// --- File browser preferences ---
window._filePrefsDefaults = { showHiddenFiles: false, confirmBeforeDelete: true, defaultSort: 'default', defaultSortDir: 'asc', breadcrumbCollapse: 4, defaultViewMode: 'list', mosaicTileSize: 3, showPinned: true, showGroups: true };
window._filePrefsCache = { ...window._filePrefsDefaults };

// Fetch once at script load — shared across all filePreferences() instances.
window._filePrefsReady = fetch('/api/v1/settings/files/preferences', { credentials: 'same-origin' })
  .then(r => r.ok ? r.json() : null)
  .then(data => {
    if (data && data.value && typeof data.value === 'object') {
      window._filePrefsCache = { ...window._filePrefsDefaults, ...data.value };
      window.dispatchEvent(new CustomEvent('preferences-changed', { detail: window._filePrefsCache }));
    }
  })
  .catch(() => {});

window.getFilePrefs = function() {
  return window._filePrefsCache;
};

window.filePreferences = function filePreferences() {
  const API_URL = '/api/v1/settings/files/preferences';

  return {
    prefs: { ...window._filePrefsCache },
    _saveTimer: null,

    async init() {
      await window._filePrefsReady;
      this.prefs = { ...window._filePrefsCache };
      window.addEventListener('preferences-changed', (e) => {
        this.prefs = { ...e.detail };
      });
    },

    update(key, value) {
      this.prefs[key] = value;
      this._saveRemote();
      this._broadcast();
      // Breadcrumb collapse is rendered server-side; refresh to apply
      if (key === 'breadcrumbCollapse') {
        this.$ajax(window.location.pathname + window.location.search, { target: 'folder-browser' });
      }
    },

    _broadcast() {
      window._filePrefsCache = { ...this.prefs };
      window.dispatchEvent(new CustomEvent('preferences-changed', { detail: this.prefs }));
    },

    _saveRemote() {
      clearTimeout(this._saveTimer);
      this._saveTimer = setTimeout(() => {
        const csrfToken = getCSRFToken();
        fetch(API_URL, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify({ value: this.prefs }),
        }).catch(() => {});
      }, 500);
    },
  };
};

window.sidebarCollapse = function sidebarCollapse() {
  return {
    collapsed: localStorage.getItem('sidebarCollapsed') === 'true',
    activeView: null,
    showPinned: window._filePrefsCache.showPinned !== false,
    showGroups: window._filePrefsCache.showGroups !== false,

    isMobile() {
      return window.matchMedia('(max-width: 1023px)').matches;
    },

    init() {
      if (this.isMobile()) {
        this.collapsed = true;
      }
      this.syncActiveView();
      window.addEventListener('popstate', () => this.syncActiveView());
      window.addEventListener('nav-state-changed', () => this.syncActiveView());
      window.addEventListener('preferences-changed', (e) => {
        this.showPinned = e.detail.showPinned !== false;
        this.showGroups = e.detail.showGroups !== false;
      });
      window.matchMedia('(max-width: 1023px)').addEventListener('change', (event) => {
        if (event.matches) {
          this.collapsed = true;
        }
      });

    },

    toggleCollapse() {
      if (this.isMobile()) {
        return;
      }
      this.collapsed = !this.collapsed;
      localStorage.setItem('sidebarCollapsed', this.collapsed);
    },

    syncActiveView() {
      const browser = document.getElementById('folder-browser');
      const sidebarActive = browser ? browser.dataset.sidebarActive : null;
      this.activeView = sidebarActive || 'root';
    },

    setActiveView(view) {
      this.activeView = view;
    }
  }
}
