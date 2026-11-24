// --- File browser preferences ---
window._filePrefsDefaults = { showHiddenFiles: false, confirmBeforeDelete: true, defaultSort: 'default', defaultSortDir: 'asc', breadcrumbCollapse: 4, defaultViewMode: 'list', mosaicTileSize: 3, showPinned: true, showGroups: true, showTags: true, compactList: false, nameCollision: 'ask' };

// Initial prefs are embedded server-side via |json_script (see index.html).
// Reading from the DOM avoids a redundant fetch and the 404 a fresh user
// would otherwise hit on /api/v1/settings/files/preferences.
(function bootFilePrefs() {
  let initial = {};
  const el = document.getElementById('file-prefs-data');
  if (el) {
    try { initial = JSON.parse(el.textContent) || {}; } catch (_) { initial = {}; }
  }
  window._filePrefsCache = { ...window._filePrefsDefaults, ...initial };

  // Every consumer (filePreferences, fileTableControls, sidebarCollapse,
  // viewToggle) reads window._filePrefsCache directly in its factory body,
  // so initial state needs no event. The one broadcast after Alpine mounts
  // covers components wired only to the listener path, which would
  // otherwise sit silently on defaults.
  document.addEventListener('alpine:initialized', () => {
    window.dispatchEvent(new CustomEvent('preferences-changed', {
      detail: window._filePrefsCache,
    }));
  }, { once: true });
})();
// Kept as a resolved Promise so existing `await window._filePrefsReady`
// callers (e.g. filePreferences().init()) still work without changes.
window._filePrefsReady = Promise.resolve();

window.getFilePrefs = function() {
  return window._filePrefsCache;
};

// The one write path of the preferences: the popover and the toolbar both
// save the whole object, so each must start from what the other changed.
window.updateFilePrefs = function updateFilePrefs(changes) {
  window._filePrefsCache = { ...window._filePrefsCache, ...changes };
  window.dispatchEvent(new CustomEvent('preferences-changed', { detail: window._filePrefsCache }));
};

// Resolves to whether the server took the save.
window.saveFilePrefs = function saveFilePrefs() {
  return fetch('/api/v1/settings/files/preferences', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
    body: JSON.stringify({ value: window._filePrefsCache }),
    credentials: 'same-origin',
  }).then((resp) => resp.ok, () => false);
};

window.filePreferences = function filePreferences() {
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
      const previous = window._filePrefsCache[key];
      this._set(key, value);
      // Breadcrumb collapse and the view mode are rendered server-side:
      // save right away and re-render once the save has landed, or the
      // re-render reads the value the user just left. A save that fails
      // rolls the dialog back instead, so it never shows a value the
      // listing does not.
      if (key === 'breadcrumbCollapse' || key === 'defaultViewMode') {
        clearTimeout(this._saveTimer);
        this._saveNow().then((saved) => {
          if (saved) {
            this.$ajax(window.location.pathname + window.location.search, { target: 'folder-browser' });
            return;
          }
          this._set(key, previous);
          window.AppAlert?.error(gettext('Could not save the preference'));
        });
        return;
      }
      this._saveRemote();
    },

    _set(key, value) {
      window.updateFilePrefs({ [key]: value });
      this.prefs = { ...window._filePrefsCache };
    },

    _saveRemote() {
      clearTimeout(this._saveTimer);
      this._saveTimer = setTimeout(() => this._saveNow(), 500);
    },

    _saveNow() {
      return window.saveFilePrefs();
    },
  };
};

window.sidebarCollapse = function sidebarCollapse() {
  return {
    // Seeded synchronously so Alpine's first bind matches the width the
    // server rendered: the rail below `lg`, the stored preference above.
    collapsed: window.matchMedia('(max-width: 1023px)').matches
      || window.sidebarPreference.initial(),
    activeView: null,
    showPinned: window._filePrefsCache.showPinned !== false,
    showGroups: window._filePrefsCache.showGroups !== false,
    showTags: window._filePrefsCache.showTags !== false,

    isMobile() {
      return window.matchMedia('(max-width: 1023px)').matches;
    },

    init() {
      this.syncActiveView();
      window.addEventListener('nav-state-changed', () => this.syncActiveView());
      window.addEventListener('preferences-changed', (e) => {
        this.showPinned = e.detail.showPinned !== false;
        this.showGroups = e.detail.showGroups !== false;
        this.showTags = e.detail.showTags !== false;
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
      window.sidebarPreference.save('files', this.collapsed);
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
