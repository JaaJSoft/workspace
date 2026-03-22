window.commandPaletteDropdown = function () {
  const STORAGE_KEY = 'workspace:recentCommands';
  const MAX_QUICK_ACTIONS = 5;
  const allCommands = JSON.parse(
    document.getElementById('workspace-commands')?.textContent || '[]'
  );

  function getRecentCommands() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
        .sort((a, b) => b.ts - a.ts);
    } catch {
      return [];
    }
  }

  function computeQuickActions() {
    const recent = getRecentCommands();
    const seen = new Set();
    const result = [];

    for (const entry of recent) {
      const cmd = allCommands.find(c => c.url === entry.url);
      if (cmd && !seen.has(cmd.url)) {
        result.push(cmd);
        seen.add(cmd.url);
      }
      if (result.length >= MAX_QUICK_ACTIONS) break;
    }

    for (const cmd of allCommands) {
      if (result.length >= MAX_QUICK_ACTIONS) break;
      if (!seen.has(cmd.url)) {
        result.push(cmd);
        seen.add(cmd.url);
      }
    }

    return result;
  }

  return {
    open: false,
    query: '',
    commands: [],
    results: [],
    hasMore: false,
    loading: false,
    loadingMore: false,
    searchQuery: '',
    activeIndex: -1,
    quickActions: [],
    _cachedItems: null,
    _cacheKey: '',

    init() {
      this.quickActions = computeQuickActions();

      if (!window.__commandPaletteShortcutBound) {
        document.addEventListener('keydown', (e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
            e.preventDefault();
            const input = document.getElementById('dashboard-search')?.querySelector('input')
              || document.querySelector('[x-data*="commandPaletteDropdown"]')?.querySelector('input');
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
          this.quickActions = computeQuickActions();
        }
      });

      this.$watch('query', () => {
        this._cachedItems = null;
        this._cacheKey = '';
        this.activeIndex = -1;
      });

      this.$watch('commands', () => {
        this._cachedItems = null;
        this._cacheKey = '';
      });

      this.$watch('results', () => {
        this._cachedItems = null;
        this._cacheKey = '';
        this.activeIndex = -1;
      });
    },

    trackCommand(url) {
      try {
        let recent = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
        recent = recent.filter(e => e.url !== url);
        recent.unshift({ url, ts: Date.now() });
        recent = recent.slice(0, 20);
        localStorage.setItem(STORAGE_KEY, JSON.stringify(recent));
      } catch {}
    },

    search() {
      this.searchQuery = this.query;
      if (this.query.length < 2) {
        this.commands = [];
        this.results = [];
        this.activeIndex = -1;
        return;
      }

      this.loading = true;
      const q = encodeURIComponent(this.query);
      fetch(`/api/v1/search?q=${q}`, { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => {
          this.commands = data.commands || [];
          this.results = data.results || [];
          this.loading = false;
        })
        .catch(() => {
          this.commands = [];
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
      this.commands = [];
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

      const cacheKey = `${this.query.length}-${this.commands.length}-${this.results.length}`;
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
        return this.commands.length + this.results.length;
      }
      if (this.query.length === 0) {
        return this.quickActions.length;
      }
      return 0;
    },

    isCommandActive(index) {
      if (!this.open) return false;
      if (this.activeIndex < 0) return false;
      if (this.query.length >= 2) {
        return this.activeIndex === index;
      }
      return false;
    },

    isResultActive(index) {
      if (!this.open) return false;
      if (this.activeIndex < 0) return false;
      if (this.query.length >= 2) {
        return this.activeIndex === this.commands.length + index;
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
