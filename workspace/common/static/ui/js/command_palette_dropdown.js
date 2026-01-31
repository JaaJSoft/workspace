window.commandPaletteDropdown = function () {
  return {
    open: false,
    query: '',
    results: [],
    hasMore: false,
    loading: false,
    loadingMore: false,
    searchQuery: '',
    activeIndex: -1,
    _cachedItems: null,
    _cacheKey: '',

    init() {
      if (!window.__commandPaletteShortcutBound) {
        document.addEventListener('keydown', (e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
            e.preventDefault();
            const input = document.querySelector('[x-data*="commandPaletteDropdown"]')?.querySelector('input');
            input?.focus();
            input?.select?.();
          }
        });
        window.__commandPaletteShortcutBound = true;
      }

      this.$watch('open', (value) => {
        this._cachedItems = null;
        this._cacheKey = '';
        if (value) {
          this.activeIndex = -1;
        }
      });

      this.$watch('query', () => {
        this._cachedItems = null;
        this._cacheKey = '';
        this.activeIndex = -1;
      });

      this.$watch('results', () => {
        this._cachedItems = null;
        this._cacheKey = '';
        this.activeIndex = -1;
      });
    },

    search() {
      this.searchQuery = this.query;
      if (this.query.length < 2) {
        this.results = [];
        this.activeIndex = -1;
        return;
      }

      this.loading = true;
      const q = encodeURIComponent(this.query);
      fetch(`/api/v1/search?q=${q}`, { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => {
          this.results = data.results || [];
          this.loading = false;
          if (window.lucide?.createIcons) {
            this.$nextTick(() => window.lucide.createIcons());
          }
        })
        .catch(() => {
          this.results = [];
          this.loading = false;
        });
    },

    loadMore() {
      this.loadingMore = true;
      setTimeout(() => {
        this.loadingMore = false;
      }, 500);
    },

    close() {
      this.open = false;
      this.query = '';
      this.results = [];
      this.activeIndex = -1;
      this._cachedItems = null;
      this._cacheKey = '';
    },

    highlightMatch(text, query) {
      if (!query) return text;
      const regex = new RegExp(`(${query})`, 'gi');
      return text.replace(regex, '<mark class="bg-primary/20">$1</mark>');
    },

    setActiveFromElement(el) {
      this._cachedItems = null;
      this._cacheKey = '';
      const items = this.getAllItems();
      const index = items.indexOf(el);
      if (index !== -1) {
        this.activeIndex = index;
      }
    },

    navigate(direction) {
      if (!this.open) return;

      const itemCount = this.getItemCount();
      if (itemCount === 0) return;

      this.activeIndex += direction;
      if (this.activeIndex < 0) {
        this.activeIndex = itemCount - 1;
      } else if (this.activeIndex >= itemCount) {
        this.activeIndex = 0;
      }

      this.$nextTick(() => {
        const allItems = this.getAllItems();
        const activeEl = allItems[this.activeIndex];
        if (activeEl) {
          activeEl.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
      });
    },

    navigateDown() {
      this.navigate(1);
    },

    navigateUp() {
      this.navigate(-1);
    },

    select() {
      if (this.activeIndex < 0) return;

      const allItems = this.getAllItems();
      const activeEl = allItems[this.activeIndex];

      if (activeEl?.href) {
        activeEl.click();
      }
    },

    selectCurrent() {
      this.select();
    },

    getAllItems() {
      if (!this.open) return [];

      const cacheKey = `${this.query.length}-${this.results.length}`;
      if (this._cachedItems && this._cacheKey === cacheKey) {
        return this._cachedItems;
      }

      const root = this.$root;
      const allLinks = Array.from(root.querySelectorAll('a[href]'));
      const visibleLinks = allLinks.filter(el => {
        let current = el;
        while (current && current !== root) {
          const style = window.getComputedStyle(current);
          if (style.display === 'none') {
            return false;
          }
          current = current.parentElement;
        }
        return true;
      });

      this._cachedItems = visibleLinks;
      this._cacheKey = cacheKey;
      return visibleLinks;
    },

    getItemCount() {
      if (this.query.length >= 2) {
        return this.results.length;
      }
      if (this.query.length === 0) {
        const quickActionsContainer = this.$root.querySelector('[x-show="query.length === 0"]');
        if (quickActionsContainer) {
          return quickActionsContainer.querySelectorAll('a[href]').length;
        }
        return 3;
      }
      return 0;
    },

    isResultActive(index) {
      if (!this.open) return false;
      if (this.activeIndex < 0) return false;
      if (this.query.length >= 2) {
        return this.activeIndex === index;
      }
      return false;
    },

    isQuickActionActive(index) {
      if (!this.open) return false;
      if (this.activeIndex < 0) return false;
      if (this.query.length === 0) {
        return this.activeIndex === index;
      }
      return false;
    },

    isActive(el) {
      if (!this.open) return false;
      if (this.activeIndex < 0) return false;

      const root = this.$root;
      const allLinks = Array.from(root.querySelectorAll('a[href]'));
      const visibleLinks = allLinks.filter(link => {
        let current = link;
        while (current && current !== root) {
          const style = window.getComputedStyle(current);
          if (style.display === 'none') return false;
          current = current.parentElement;
        }
        return true;
      });

      return visibleLinks.indexOf(el) === this.activeIndex;
    }
  };
};
