// The sentinel at the end of a page of photos, which appends the next page
// as it scrolls into view. Shared by the Photos pages and by the page of a
// public album link.

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
      // viewport never scrolls on the Photos pages. A page without one (a
      // public album link) scrolls the viewport, the root null stands for.
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
