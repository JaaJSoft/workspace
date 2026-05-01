// Right-side panels: info panel (stats + shared media) and search panel
// (in-conversation message search with filters and scroll-to-message).
window.chatPanelsMixin = function chatPanelsMixin() {
  return {
    // ── Info panel state ─────────────────────────────────────
    showInfoPanel: false,
    conversationStats: null,
    loadingStats: false,
    conversationMedia: [],
    conversationMediaTotal: 0,
    loadingMedia: false,
    loadingMoreMedia: false,
    mediaFilter: 'images',  // 'images', 'files', 'all'

    // ── Search panel state ───────────────────────────────────
    showSearchPanel: false,
    searchQuery: '',
    searchResults: [],
    searchLoading: false,
    searchHighlight: -1,
    searchFilterAuthor: '',
    searchFilterDateRange: '',
    searchFilterDateFrom: '',
    searchFilterDateTo: '',
    searchFilterHasFiles: false,
    searchFilterHasImages: false,
    searchFiltersExpanded: false,

    // ── Info panel ───────────────────────────────────────────
    toggleInfoPanel() {
      this.showInfoPanel = !this.showInfoPanel;
      if (this.showInfoPanel) {
        this.closeSearchPanel();
        if (this.activeConversation) {
          this.loadConversationStats(this.activeConversation.uuid);
          this.loadPinnedMessages(this.activeConversation.uuid);
          this.loadConversationMedia(this.activeConversation.uuid);
          if (this.isBotConversation(this.activeConversation)) {
            this.loadBotMemories();
            this.loadScheduledMessages(this.activeConversation.uuid);
          }
        }
      }
    },

    async loadConversationStats(conversationId) {
      this.loadingStats = true;
      this.conversationStats = null;
      try {
        const resp = await fetch(`/api/v1/chat/conversations/${conversationId}/stats`, {
          credentials: 'same-origin',
        });
        if (resp.ok) {
          this.conversationStats = await resp.json();
        }
      } catch (e) {
        console.error('Failed to load conversation stats', e);
      }
      this.loadingStats = false;
    },

    async loadConversationMedia(conversationId, append = false) {
      if (append) {
        this.loadingMoreMedia = true;
      } else {
        this.loadingMedia = true;
        this.conversationMedia = [];
        this.conversationMediaTotal = 0;
      }
      const offset = append ? this.conversationMedia.length : 0;
      try {
        const resp = await fetch(
          `/api/v1/chat/conversations/${conversationId}/medias?type=${this.mediaFilter}&offset=${offset}&limit=24`,
          { credentials: 'same-origin' },
        );
        if (resp.ok) {
          const data = await resp.json();
          if (append) {
            this.conversationMedia.push(...data.results);
          } else {
            this.conversationMedia = data.results;
          }
          this.conversationMediaTotal = data.total;
        }
      } catch (e) {
        console.error('Failed to load conversation media', e);
      }
      this.loadingMedia = false;
      this.loadingMoreMedia = false;
    },

    loadMoreMedia() {
      if (!this.activeConversation || this.loadingMoreMedia) return;
      this.loadConversationMedia(this.activeConversation.uuid, true);
    },

    changeMediaFilter(filter) {
      this.mediaFilter = filter;
      if (this.activeConversation) {
        this.loadConversationMedia(this.activeConversation.uuid);
      }
    },

    // ── Search panel ─────────────────────────────────────────
    toggleSearchPanel() {
      this.showSearchPanel = !this.showSearchPanel;
      if (this.showSearchPanel) {
        this.showInfoPanel = false;
        this.$nextTick(() => {
          this.$refs.searchInput?.focus();
        });
      } else {
        this.closeSearchPanel();
      }
    },

    closeSearchPanel() {
      this.showSearchPanel = false;
      this.searchQuery = '';
      this.searchResults = [];
      this.searchLoading = false;
      this.searchHighlight = -1;
      this.searchFilterAuthor = '';
      this.searchFilterDateRange = '';
      this.searchFilterDateFrom = '';
      this.searchFilterDateTo = '';
      this.searchFilterHasFiles = false;
      this.searchFilterHasImages = false;
      this.searchFiltersExpanded = false;
    },

    hasActiveSearchFilters() {
      return !!(this.searchFilterAuthor || this.searchFilterDateRange ||
        this.searchFilterDateFrom || this.searchFilterDateTo ||
        this.searchFilterHasFiles || this.searchFilterHasImages);
    },

    clearSearchFilters() {
      this.searchFilterAuthor = '';
      this.searchFilterDateRange = '';
      this.searchFilterDateFrom = '';
      this.searchFilterDateTo = '';
      this.searchFilterHasFiles = false;
      this.searchFilterHasImages = false;
      this.searchMessages();
    },

    async searchMessages() {
      const q = (this.searchQuery || '').trim();
      const filtersActive = this.hasActiveSearchFilters();
      if (q.length < 2 && !filtersActive) {
        this.searchResults = [];
        return;
      }
      if (!this.activeConversation) return;

      this.searchLoading = true;
      try {
        const params = new URLSearchParams();
        if (q.length >= 2) params.set('q', q);
        if (this.searchFilterAuthor) params.set('author', this.searchFilterAuthor);
        if (this.searchFilterDateRange && this.searchFilterDateRange !== 'custom') {
          params.set('date_range', this.searchFilterDateRange);
        }
        if (this.searchFilterDateRange === 'custom') {
          if (this.searchFilterDateFrom) params.set('date_from', this.searchFilterDateFrom);
          if (this.searchFilterDateTo) params.set('date_to', this.searchFilterDateTo);
        }
        if (this.searchFilterHasFiles) params.set('has_files', 'true');
        if (this.searchFilterHasImages) params.set('has_images', 'true');

        const resp = await fetch(
          `/api/v1/chat/conversations/${this.activeConversation.uuid}/messages/search?${params}`,
          { credentials: 'same-origin' }
        );
        if (resp.ok) {
          const data = await resp.json();
          this.searchResults = data.results || [];
          this.searchHighlight = -1;
        }
      } catch (e) {
        console.error('Failed to search messages', e);
      }
      this.searchLoading = false;
    },

    handleSearchKeydown(e) {
      if (!this.searchResults.length) return;

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        this.searchHighlight = (this.searchHighlight + 1) % this.searchResults.length;
        this._scrollSearchResultIntoView();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        this.searchHighlight = this.searchHighlight <= 0
          ? this.searchResults.length - 1
          : this.searchHighlight - 1;
        this._scrollSearchResultIntoView();
      } else if (e.key === 'Enter' && this.searchHighlight >= 0) {
        e.preventDefault();
        this.scrollToMessage(this.searchResults[this.searchHighlight].uuid);
      }
    },

    _scrollSearchResultIntoView() {
      this.$nextTick(() => {
        const el = document.querySelector('[data-search-result-active="true"]');
        if (el) el.scrollIntoView({ block: 'nearest' });
      });
    },

    highlightMatch(bodyHtml, query) {
      if (!query || !bodyHtml) return bodyHtml;
      const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const regex = new RegExp(`(${escaped})`, 'gi');
      return bodyHtml.replace(regex, '<mark class="bg-warning/40 rounded px-0.5">$1</mark>');
    },

    scrollToMessage(uuid) {
      const el = document.getElementById(`msg-${uuid}`);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.classList.add('ring-2', 'ring-warning', 'ring-offset-2', 'ring-offset-base-100');
        setTimeout(() => {
          el.classList.remove('ring-2', 'ring-warning', 'ring-offset-2', 'ring-offset-base-100');
        }, 2000);
        return;
      }
      // Message not loaded yet — load all then retry
      this._loadAllAndScrollTo(uuid);
    },

    async _loadAllAndScrollTo(uuid) {
      // Keep loading older messages until we find it or run out
      let attempts = 0;
      while (this.hasMoreMessages && attempts < 20) {
        await this.loadMoreMessages();
        attempts++;
        const el = document.getElementById(`msg-${uuid}`);
        if (el) {
          await this.$nextTick();
          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          el.classList.add('ring-2', 'ring-warning', 'ring-offset-2', 'ring-offset-base-100');
          setTimeout(() => {
            el.classList.remove('ring-2', 'ring-warning', 'ring-offset-2', 'ring-offset-base-100');
          }, 2000);
          return;
        }
      }
    },
  };
};
