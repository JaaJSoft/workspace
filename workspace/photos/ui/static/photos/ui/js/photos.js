// Photos: the module shell (sidebar, viewer hand-off) and the sentinel that
// appends the next page of the timeline as it scrolls into view.

const PHOTOS_MOBILE_QUERY = '(max-width: 1023px)';

// The swap targets of this page. A response that lacks one (a login page once
// the session expired) must leave the live element alone: alpine-ajax's
// default is to remove it, which would empty the timeline.
const PHOTOS_SWAP_TARGETS = ['photos-nav', 'photos-content', 'timeline-grid', 'timeline-more'];

window.photosApp = function photosApp() {
  return {
    collapsed: window.sidebarPreference.initial(),

    // The stored preference is a desktop one: below `lg` the drawer is
    // off-canvas and opens as the full sidebar.
    sidebarCollapsed() {
      if (window.matchMedia(PHOTOS_MOBILE_QUERY).matches) return false;
      return this.collapsed;
    },

    toggleCollapse() {
      if (window.matchMedia(PHOTOS_MOBILE_QUERY).matches) return;
      this.collapsed = !this.collapsed;
      window.sidebarPreference.save('photos', this.collapsed);
    },

    closeDrawer() {
      const toggle = document.getElementById('photos-drawer');
      if (toggle) toggle.checked = false;
    },

    keepMissingTarget(event) {
      const target = event.detail && event.detail.target;
      if (target && PHOTOS_SWAP_TARGETS.includes(target.id)) {
        event.preventDefault();
      }
    },

    openPhoto(tile) {
      window.dispatchEvent(new CustomEvent('open-file-viewer', {
        detail: {
          uuid: tile.dataset.uuid,
          name: tile.dataset.displayName,
          type: tile.dataset.fileType,
        },
      }));
    },
  };
};

// Each page ends with its own sentinel, which the next page replaces: the
// observer lives and dies with the element it watches, so there is never
// more than one, and never one watching a page that is already loaded.
window.timelineSentinel = function timelineSentinel(url) {
  return {
    loading: false,
    _observer: null,

    init() {
      if (!('IntersectionObserver' in window)) return;
      // Observed against the scroll container, not the viewport: the margin
      // only reaches ahead of the fold relative to the root, and the
      // viewport never scrolls on this page.
      this._observer = new IntersectionObserver(
        (entries) => {
          if (entries.some((entry) => entry.isIntersecting)) this.load();
        },
        { root: this.$el.closest('#photos-content'), rootMargin: '0px 0px 800px 0px' },
      );
      this._observer.observe(this.$el);
    },

    destroy() {
      if (this._observer) this._observer.disconnect();
    },

    async load() {
      if (this.loading) return;
      this.loading = true;
      try {
        await this.$ajax(url, { targets: ['timeline-grid', 'timeline-more'], focus: false });
      } catch (_) {
        // The button stays for a retry; the observer will not fire again
        // while the sentinel sits still in view.
      }
      this.loading = false;
    },
  };
};
