window.commandPaletteDropdown = function () {
  const STORAGE_KEY = 'workspace:recentCommands';
  const MAX_QUICK_ACTIONS = 5;
  // VS Code's idiom: a leading `>` narrows the palette to actions and apps,
  // whatever comes after it is matched against the command list alone.
  const COMMAND_PREFIX = '>';
  const MIN_SEARCH_LENGTH = 2;
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

  // Same ranking as ModuleRegistry.search_commands: name hits first, keyword
  // hits after, each group in registration order (allCommands is pre-sorted).
  function filterCommands(term) {
    const q = term.toLowerCase();
    if (!q) return allCommands.slice();
    const nameMatches = [];
    const keywordMatches = [];
    for (const cmd of allCommands) {
      if (cmd.name.toLowerCase().includes(q)) {
        nameMatches.push(cmd);
      } else if ((cmd.keywords || []).some(kw => kw.toLowerCase().includes(q))) {
        keywordMatches.push(cmd);
      }
    }
    return nameMatches.concat(keywordMatches);
  }

  return {
    open: false,
    query: '',
    commands: [],
    results: [],
    loading: false,
    searchQuery: '',
    // Bumped per search(); a response whose id no longer matches is stale.
    _searchRequestId: 0,
    activeIndex: -1,
    quickActions: [],
    _cachedItems: null,
    _cacheKey: '',

    init() {
      this.quickActions = computeQuickActions();

      if (!window.__commandPaletteShortcutBound) {
        document.addEventListener('keydown', (e) => {
          // Shift makes the browser report 'K', hence the case fold.
          if (!(e.metaKey || e.ctrlKey) || (e.key || '').toLowerCase() !== 'k') return;
          e.preventDefault();
          const input = document.getElementById('dashboard-search')?.querySelector('input')
            || document.querySelector('[x-data*="commandPaletteDropdown"]')?.querySelector('input');
          if (!input) return;
          if (e.shiftKey) {
            // The listener is bound once for the page, so it cannot reach the
            // component's state directly; the input's own palette picks the
            // event up through @palette:commands.
            input.dispatchEvent(new CustomEvent('palette:commands'));
            return;
          }
          input.focus();
          input.select?.();
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

    isCommandMode() {
      return this.query.startsWith(COMMAND_PREFIX);
    },

    commandTerm() {
      return this.query.slice(COMMAND_PREFIX.length).trim();
    },

    showQuickActions() {
      return this.query.length === 0;
    },

    showResults() {
      return this.isCommandMode() || this.query.length >= MIN_SEARCH_LENGTH;
    },

    enterCommandMode() {
      this.query = COMMAND_PREFIX;
      this.open = true;
      this.search();
      this.$nextTick(() => {
        const input = this.$refs.input;
        if (!input) return;
        input.focus();
        input.setSelectionRange?.(COMMAND_PREFIX.length, COMMAND_PREFIX.length);
      });
    },

    search() {
      const requestId = ++this._searchRequestId;
      if (this.isCommandMode()) {
        this.searchQuery = this.commandTerm();
        this.commands = filterCommands(this.searchQuery);
        this.results = [];
        this.activeIndex = -1;
        this.loading = false;
        return;
      }
      this.searchQuery = this.query;
      if (this.query.length < MIN_SEARCH_LENGTH) {
        this.commands = [];
        this.results = [];
        this.activeIndex = -1;
        this.loading = false;
        return;
      }

      this.loading = true;
      const q = encodeURIComponent(this.query);
      fetch(`/api/v1/search?q=${q}`, { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => {
          if (requestId !== this._searchRequestId) return;
          this.commands = data.commands || [];
          this.results = data.results || [];
          this.loading = false;
        })
        .catch(() => {
          if (requestId !== this._searchRequestId) return;
          this.commands = [];
          this.results = [];
          this.loading = false;
        });
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
      if (this.showResults()) {
        return this.commands.length + this.results.length;
      }
      if (this.showQuickActions()) {
        return this.quickActions.length;
      }
      return 0;
    },

    isCommandActive(index) {
      if (!this.open) return false;
      if (this.activeIndex < 0) return false;
      return this.showResults() && this.activeIndex === index;
    },

    isResultActive(index) {
      if (!this.open) return false;
      if (this.activeIndex < 0) return false;
      return this.showResults() && this.activeIndex === this.commands.length + index;
    },

    isQuickActionActive(index) {
      if (!this.open) return false;
      if (this.activeIndex < 0) return false;
      return this.showQuickActions() && this.activeIndex === index;
    },

    isActive(el) {
      if (!this.open) return false;
      if (this.activeIndex < 0) return false;
      return this.getAllItems().indexOf(el) === this.activeIndex;
    }
  };
};
