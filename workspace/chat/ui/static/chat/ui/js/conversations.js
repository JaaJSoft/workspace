// Conversation list + selection + drafts + new-conversation dialog
// (with user search), conversation pin / unpin / drag-drop reorder,
// list-level context menu, conversation display helpers (name, avatar,
// member list).
window.chatConversationsMixin = function chatConversationsMixin() {
  // Read the URL-targeted conversation UUID synchronously from the embedded
  // JSON so the first Alpine binding pass already knows we're going to have
  // an active conversation. Without this, on mobile, `/chat/<uuid>` paints
  // the drawer sidebar visible (because `activeConversation` is still null),
  // then `init()` awaits `selectConversationById`, sets `activeConversation`,
  // and the `:style` binding finally hides the drawer — visible flash. The
  // flag is consumed in init() right after the conversation is loaded.
  let pendingUuid = null;
  try {
    const el = document.getElementById('initial-conversation');
    if (el) pendingUuid = JSON.parse(el.textContent) || null;
  } catch (_) {
    pendingUuid = null;
  }

  return {
    // ── State ────────────────────────────────────────────────
    conversations: [],
    activeConversation: null,
    pendingInitialConvUuid: pendingUuid,

    // New-conversation dialog state
    selectedUsers: [],
    newConvTitle: '',
    creatingConversation: false,
    userSearchQuery: '',
    userSearchResults: [],
    userSearchLoading: false,
    userSearchShowDropdown: false,
    userSearchHighlight: -1,

    // Pinned drag & drop
    draggingPinned: null,
    dragOverPinned: null,

    // Context menu
    ctxMenu: { open: false, x: 0, y: 0, uuid: null, kind: null, isPinned: false, isBot: false },

    // ── List loading ─────────────────────────────────────────
    async loadConversations() {
      try {
        const resp = await fetch('/api/v1/chat/conversations', { credentials: 'same-origin' });
        if (resp.ok) {
          this.conversations = await resp.json();
        }
      } catch (e) {
        console.error('Failed to load conversations', e);
      }
    },

    refreshConversationList() {
      this.$el.addEventListener('ajax:after', () => {
        const target = document.getElementById('conversation-list');
        if (!target) return;
        for (const conv of this.conversations) {
          if (conv._avatar_bust) {
            const img = target.querySelector(`img[src*="/conversations/${conv.uuid}/avatar/"]`);
            if (img) img.src = `/api/v1/chat/conversations/${conv.uuid}/avatar/image?t=${conv._avatar_bust}`;
          }
        }
      }, { once: true });
      this.$ajax('/chat/conversations', { target: 'conversation-list' });
    },

    async selectConversationById(uuid, updateUrl = true) {
      let conv = this.conversations.find(c => c.uuid === uuid);
      if (!conv) {
        try {
          const resp = await fetch(`/api/v1/chat/conversations/${uuid}`, { credentials: 'same-origin' });
          if (!resp.ok) return;
          conv = await resp.json();
        } catch (e) {
          console.error('Failed to load conversation', e);
          return;
        }
      }
      await this.selectConversation(conv, updateUrl);
    },

    // ── Drafts ───────────────────────────────────────────────
    _saveDraft() {
      if (!this.activeConversation) return;
      const key = `chat_draft_${this.activeConversation.uuid}`;
      const body = this.messageBody.trim();
      if (body) {
        localStorage.setItem(key, body);
      } else {
        localStorage.removeItem(key);
      }
    },

    _restoreDraft(convUuid) {
      const key = `chat_draft_${convUuid}`;
      return localStorage.getItem(key) || '';
    },

    _clearDraft(convUuid) {
      localStorage.removeItem(`chat_draft_${convUuid || this.activeConversation?.uuid}`);
    },

    async selectConversation(conv, updateUrl = true) {
      // Save draft of current conversation before switching
      this._saveDraft();

      this.activeConversation = conv;
      this.hasMoreMessages = false;
      this.editingMessageUuid = null;
      this.replyingTo = null;
      this.messageBody = this._restoreDraft(conv.uuid);
      // Revoke any object URLs from previous pending file previews so
      // dropped attachments don't leak blob:// URLs across conversation
      // switches (image / video previews allocate a URL via
      // URL.createObjectURL in input.js addFiles).
      for (const f of this.pendingFiles || []) {
        if (f._preview) URL.revokeObjectURL(f._preview);
      }
      this.pendingFiles = [];
      this.pinnedMessages = [];
      this.botTyping = false;
      this.showInfoPanel = false;
      this.conversationStats = null;
      this.conversationMedia = [];
      this.conversationMediaTotal = 0;
      this.mediaFilter = 'images';
      this.botMemories = [];
      this.memorySearch = '';
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

      if (updateUrl) {
        history.pushState({ conversationUuid: conv.uuid }, '', `/chat/${conv.uuid}`);
      }

      // Wait for Alpine to render the x-if="activeConversation" template
      // so that #messages-container exists in the DOM
      await this.$nextTick();

      await this.loadMessages(conv.uuid);

      // Restore bot typing indicator if there's an active AI task
      const msgList = document.getElementById('message-list');
      if (msgList?.dataset.botProcessing === 'true') {
        this.botTyping = true;
      }

      await this.markAsRead(conv.uuid);
      await this.loadPinnedMessages(conv.uuid);

      conv.unread_count = 0;

      // Double $nextTick: first lets Alpine render, second lets the browser layout
      this.$nextTick(() => {
        this.$nextTick(() => {
          this.scrollToBottom(true);
          this.getMessageInput()?.focus();
        });
      });
    },

    // ── New conversation dialog + user search ────────────────
    showNewConversationDialog() {
      this.selectedUsers = [];
      this.newConvTitle = '';
      this.userSearchQuery = '';
      this.userSearchResults = [];
      this.userSearchShowDropdown = false;
      this.$refs.newConvDialog.showModal();
      this.$nextTick(() => {
        this.$refs.userSearchInput?.focus();
      });
    },

    async searchUsers() {
      const q = (this.userSearchQuery || '').trim();
      if (q.length < 2) {
        this.userSearchResults = [];
        this.userSearchShowDropdown = false;
        return;
      }
      this.userSearchLoading = true;
      try {
        const resp = await fetch(`/api/v1/users/search?q=${encodeURIComponent(q)}&limit=10`, {
          credentials: 'same-origin',
        });
        if (resp.ok) {
          const data = await resp.json();
          const selectedIds = new Set(this.selectedUsers.map(u => u.id));
          this.userSearchResults = (data.results || []).filter(
            u => u.id !== this.currentUserId && !selectedIds.has(u.id)
          );
          this.userSearchHighlight = -1;
          this.userSearchShowDropdown = true;
        }
      } catch (e) {
        console.error('User search failed', e);
      }
      this.userSearchLoading = false;
    },

    selectSearchedUser(user) {
      this.addSelectedUser(user);
      this.userSearchQuery = '';
      this.userSearchResults = [];
      this.userSearchHighlight = -1;
      this.userSearchShowDropdown = false;
    },

    handleUserSearchKeydown(e) {
      const results = this.userSearchResults;
      const dropdownOpen = this.userSearchShowDropdown && results.length > 0;

      if (e.key === 'ArrowDown' && dropdownOpen) {
        e.preventDefault();
        this.userSearchHighlight = (this.userSearchHighlight + 1) % results.length;
        this._scrollSearchHighlightIntoView();
      } else if (e.key === 'ArrowUp' && dropdownOpen) {
        e.preventDefault();
        this.userSearchHighlight = this.userSearchHighlight <= 0
          ? results.length - 1
          : this.userSearchHighlight - 1;
        this._scrollSearchHighlightIntoView();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (dropdownOpen && this.userSearchHighlight >= 0 && this.userSearchHighlight < results.length) {
          this.selectSearchedUser(results[this.userSearchHighlight]);
          this.$refs.userSearchInput?.focus();
        } else if (this.selectedUsers.length > 0 && !this.userSearchQuery.trim()) {
          this.createConversation();
        }
      }
    },

    _scrollSearchHighlightIntoView() {
      this.$nextTick(() => {
        const el = document.querySelector('[data-search-active="true"]');
        if (el) el.scrollIntoView({ block: 'nearest' });
      });
    },

    addSelectedUser(user) {
      if (user.id === this.currentUserId) return;
      if (this.selectedUsers.find(u => u.id === user.id)) return;
      this.selectedUsers.push(user);
    },

    removeSelectedUser(userId) {
      this.selectedUsers = this.selectedUsers.filter(u => u.id !== userId);
    },

    async createConversation() {
      if (this.selectedUsers.length === 0) return;
      this.creatingConversation = true;

      const memberIds = this.selectedUsers.map(u => u.id);
      const payload = { member_ids: memberIds };
      if (this.selectedUsers.length >= 2 && this.newConvTitle.trim()) {
        payload.title = this.newConvTitle.trim();
      }

      try {
        const resp = await fetch('/api/v1/chat/conversations', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
          },
          credentials: 'same-origin',
          body: JSON.stringify(payload),
        });

        if (resp.ok) {
          const conv = await resp.json();
          this.$refs.newConvDialog.close();

          this.conversations.unshift(conv);
          this.refreshConversationList();
          await this.selectConversation(conv);
        }
      } catch (e) {
        console.error('Failed to create conversation', e);
      }
      this.creatingConversation = false;
    },

    async _openDmByUserId(userId) {
      try {
        const resp = await fetch('/api/v1/chat/conversations', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
          },
          credentials: 'same-origin',
          body: JSON.stringify({ member_ids: [userId] }),
        });
        if (resp.ok) {
          const conv = await resp.json();
          if (!this.conversations.find(c => c.uuid === conv.uuid)) {
            this.conversations.unshift(conv);
            this.refreshConversationList();
          }
          history.replaceState({ conversationUuid: conv.uuid }, '', `/chat/${conv.uuid}`);
          await this.selectConversationById(conv.uuid, false);
        }
      } catch (e) {
        console.error('Failed to open DM', e);
      }
    },

    // ── Display helpers ──────────────────────────────────────
    conversationName(conv) {
      if (conv.title) return conv.title;
      if (conv.kind === 'dm') {
        const other = conv.members?.find(m => m.user.id !== this.currentUserId);
        return other ? this.memberDisplayName(other) : 'Direct Message';
      }
      const names = (conv.members || [])
        .filter(m => m.user.id !== this.currentUserId)
        .map(m => this.memberDisplayName(m))
        .slice(0, 3);
      if (names.length === 0) return 'Group';
      return names.join(', ');
    },

    _avatarHtml(user, size, bgClass) {
      return window.userAvatarHtml(user.id, user.username, size);
    },

    conversationAvatar(conv) {
      if (conv.kind === 'dm') {
        const other = conv.members?.find(m => m.user.id !== this.currentUserId);
        if (other) return window.userAvatarWithCardHtml(other.user.id, other.user.username, 'w-10 h-10 text-sm');
        return `<div class="w-10 h-10 rounded-full bg-neutral text-neutral-content flex items-center justify-center flex-shrink-0"><span class="text-sm">?</span></div>`;
      }
      // Group with custom avatar
      if (conv.has_avatar) {
        const bust = conv._avatar_bust ? `?t=${conv._avatar_bust}` : '';
        return `<div class="w-10 h-10 rounded-full overflow-hidden flex-shrink-0"><img src="/api/v1/chat/conversations/${conv.uuid}/avatar/image${bust}" alt="Group avatar" class="w-full h-full object-cover" /></div>`;
      }
      const initials = (conv.members || [])
        .filter(m => m.user.id !== this.currentUserId)
        .slice(0, 2)
        .map(m => this.memberDisplayName(m)[0].toUpperCase())
        .join('');
      return `<div class="w-10 h-10 rounded-full bg-info text-info-content flex items-center justify-center flex-shrink-0"><span class="text-sm">${initials || 'G'}</span></div>`;
    },

    membersList(conv) {
      if (!conv.members) return '';
      const names = conv.members
        .filter(m => m.user.id !== this.currentUserId)
        .map(m => m.user.username);
      if (conv.kind === 'dm') return 'Direct message';
      return names.length + 1 + ' members';
    },

    // ── Conversation pinning ─────────────────────────────────
    async pinConversation(uuid) {
      try {
        const resp = await fetch(`/api/v1/chat/conversations/${uuid}/pin`, {
          method: 'POST',
          headers: { 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
        });
        if (resp.ok || resp.status === 201) {
          const conv = this.conversations.find(c => c.uuid === uuid);
          if (conv) conv.is_pinned = true;
          this.refreshConversationList();
        }
      } catch (e) {
        console.error('Failed to pin conversation', e);
      }
    },

    async unpinConversation(uuid) {
      try {
        const resp = await fetch(`/api/v1/chat/conversations/${uuid}/pin`, {
          method: 'DELETE',
          headers: { 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
        });
        if (resp.ok || resp.status === 204) {
          const conv = this.conversations.find(c => c.uuid === uuid);
          if (conv) conv.is_pinned = false;
          this.refreshConversationList();
        }
      } catch (e) {
        console.error('Failed to unpin conversation', e);
      }
    },

    onPinnedDragStart(event, uuid) {
      this.draggingPinned = uuid;
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', uuid);
      event.target.classList.add('opacity-50');
    },

    onPinnedDragEnd(event) {
      this.draggingPinned = null;
      this.dragOverPinned = null;
      event.target.classList.remove('opacity-50');
    },

    onPinnedDragOver(event, uuid) {
      if (!this.draggingPinned || this.draggingPinned === uuid) return;
      event.dataTransfer.dropEffect = 'move';
      this.dragOverPinned = uuid;
    },

    async onPinnedDrop(event, targetUuid) {
      const srcUuid = this.draggingPinned;
      this.draggingPinned = null;
      this.dragOverPinned = null;

      if (!srcUuid || srcUuid === targetUuid) return;

      // Build current pinned order from the DOM list items
      const listEl = event.target.closest('ul');
      if (!listEl) return;

      const items = [...listEl.querySelectorAll('li[draggable]')];
      const order = items.map(li => li.dataset.conversationUuid).filter(Boolean);

      // Reorder: move srcUuid before targetUuid
      const srcIdx = order.indexOf(srcUuid);
      const tgtIdx = order.indexOf(targetUuid);
      if (srcIdx === -1 || tgtIdx === -1) return;

      order.splice(srcIdx, 1);
      const newTgtIdx = order.indexOf(targetUuid);
      order.splice(newTgtIdx, 0, srcUuid);

      // Persist reorder
      try {
        await fetch('/api/v1/chat/conversations/pin-reorder', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
          },
          credentials: 'same-origin',
          body: JSON.stringify({ order }),
        });
        this.refreshConversationList();
      } catch (e) {
        console.error('Failed to reorder pinned conversations', e);
      }
    },

    // ── Conversation context menu ─────────────────────────────
    openConvContextMenu(event, uuid, kind, isPinned = false) {
      event.preventDefault();
      const menu = document.getElementById('conv-context-menu');
      if (!menu) return;

      this.ctxMenu.uuid = uuid;
      this.ctxMenu.kind = kind;
      const conv = this.conversations.find(c => c.uuid === uuid);
      this.ctxMenu.isPinned = isPinned || conv?.is_pinned || false;
      this.ctxMenu.isBot = conv?.is_bot_conversation || false;
      this.ctxMenu.open = true;

      this.$nextTick(() => {
        const rect = menu.getBoundingClientRect();
        let x = event.clientX;
        let y = event.clientY;
        if (x + rect.width > window.innerWidth) x = window.innerWidth - rect.width - 10;
        if (y + rect.height > window.innerHeight) y = window.innerHeight - rect.height - 10;
        this.ctxMenu.x = x;
        this.ctxMenu.y = y;
      });
    },

    async ctxMenuAction(action) {
      const uuid = this.ctxMenu.uuid;
      this.ctxMenu.open = false;

      // Ensure the conversation is selected first for actions that need activeConversation
      const needsActive = ['info', 'rename', 'add_members', 'leave'];
      if (needsActive.includes(action)) {
        if (!this.activeConversation || this.activeConversation.uuid !== uuid) {
          await this.selectConversationById(uuid);
        }
      }

      switch (action) {
        case 'info':
          this.showInfoPanel = true;
          if (this.activeConversation) {
            this.loadConversationStats(this.activeConversation.uuid);
            this.loadConversationMedia(this.activeConversation.uuid);
          }
          break;
        case 'copy_link':
          this.copyConversationLink(uuid);
          break;
        case 'pin':
          this.pinConversation(uuid);
          break;
        case 'unpin':
          this.unpinConversation(uuid);
          break;
        case 'rename':
          this.renameConversation();
          break;
        case 'add_members':
          this.addMembersToConversation();
          break;
        case 'leave':
          this.leaveConversation();
          break;
      }
    },
  };
};
