function chatApp(currentUserId) {
  return {
    // ── State ──────────────────────────────────────────────
    conversations: [],
    activeConversation: null,
    messageBody: '',
    loadingMessages: false,
    loadingMoreMessages: false,
    hasMoreMessages: false,
    editingMessageUuid: null,
    selectedUsers: [],
    newConvTitle: '',
    creatingConversation: false,
    userSearchQuery: '',
    userSearchResults: [],
    userSearchLoading: false,
    userSearchShowDropdown: false,
    userSearchHighlight: -1,
    currentUserId: currentUserId,
    quickEmojis: ['\ud83d\udc4d', '\u2764\ufe0f', '\ud83d\ude02', '\ud83d\ude2e', '\ud83d\ude22', '\ud83c\udf89'],
    showInfoPanel: false,
    conversationStats: null,
    loadingStats: false,
    // Add member dialog state
    addMemberSearchQuery: '',
    addMemberResults: [],
    addMemberSelected: [],
    addMemberLoading: false,
    addMemberShowDropdown: false,
    addMemberHighlight: -1,
    addMemberSaving: false,
    _linkCopied: false,
    // Search panel state
    showSearchPanel: false,
    searchQuery: '',
    searchResults: [],
    searchLoading: false,
    searchHighlight: -1,
    // Context menu state
    ctxMenu: { open: false, x: 0, y: 0, uuid: null, kind: null, isPinned: false },
    // File upload
    pendingFiles: [],
    isDraggingOver: false,
    _dragCounter: 0,
    // Pinned drag & drop
    draggingPinned: null,
    dragOverPinned: null,

    // Sidebar
    collapsed: JSON.parse(localStorage.getItem('chatSidebarCollapsed') || 'false'),

    // ── Init ───────────────────────────────────────────────
    async init() {
      // Load conversations from embedded JSON (fast first paint)
      const dataEl = document.getElementById('conversations-data');
      if (dataEl) {
        try {
          this.conversations = JSON.parse(dataEl.textContent);
        } catch (e) {
          console.error('Failed to parse embedded conversations', e);
          await this.loadConversations();
        }
      } else {
        await this.loadConversations();
      }

      // Auto-collapse on mobile
      if (this.isMobile()) {
        this.collapsed = true;
      }
      window.matchMedia('(max-width: 1023px)').addEventListener('change', (e) => {
        if (e.matches) this.collapsed = true;
      });

      // Handle browser back/forward
      window.addEventListener('popstate', (e) => {
        const uuid = e.state?.conversationUuid || null;
        if (uuid) {
          this.selectConversationById(uuid, false);
        } else {
          this.activeConversation = null;
        }
      });

      // Auto-select conversation from URL (e.g. /chat/<uuid>)
      const initialEl = document.getElementById('initial-conversation');
      if (initialEl) {
        try {
          const uuid = JSON.parse(initialEl.textContent);
          if (uuid) {
            // Replace current history entry so back goes to /chat
            history.replaceState({ conversationUuid: uuid }, '', `/chat/${uuid}`);
            await this.selectConversationById(uuid, false);
          }
        } catch (e) {
          console.error('Failed to parse initial conversation', e);
        }
      }

      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') lucide.createIcons();
      });
    },

    // ── CSRF ───────────────────────────────────────────────
    _csrf() {
      return document.querySelector('[name=csrfmiddlewaretoken]')?.value
        || document.cookie.split('; ').find(c => c.startsWith('csrftoken='))?.split('=')[1]
        || '';
    },

    // ── Sidebar collapse ───────────────────────────────────
    toggleCollapse() {
      this.collapsed = !this.collapsed;
      localStorage.setItem('chatSidebarCollapsed', JSON.stringify(this.collapsed));
      setTimeout(() => {
        if (typeof lucide !== 'undefined') lucide.createIcons();
      }, 300);
    },

    isMobile() {
      return window.matchMedia('(max-width: 1023px)').matches;
    },

    // ── Conversations ──────────────────────────────────────
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

    async refreshConversationList() {
      try {
        const resp = await fetch('/chat/conversations', {
          credentials: 'same-origin',
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
        });
        if (resp.ok) {
          const html = await resp.text();
          const parser = new DOMParser();
          const doc = parser.parseFromString(html, 'text/html');
          const newList = doc.getElementById('conversation-list');
          const target = document.getElementById('conversation-list');
          if (newList && target) {
            target.innerHTML = newList.innerHTML;
            // Bust browser memory cache for avatar images that were updated this session
            for (const conv of this.conversations) {
              if (conv.avatar_url && conv.avatar_url.includes('?t=')) {
                const img = target.querySelector(`img[src*="/conversations/${conv.uuid}/avatar/"]`);
                if (img) img.src = conv.avatar_url;
              }
            }
            this.$nextTick(() => {
              if (typeof lucide !== 'undefined') lucide.createIcons();
            });
          }
        }
      } catch (e) {
        console.error('Failed to refresh conversation list', e);
      }
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

    async selectConversation(conv, updateUrl = true) {
      this.activeConversation = conv;
      this.hasMoreMessages = false;
      this.editingMessageUuid = null;
      this.messageBody = '';
      this.pendingFiles = [];
      this.showInfoPanel = false;
      this.conversationStats = null;
      this.showSearchPanel = false;
      this.searchQuery = '';
      this.searchResults = [];
      this.searchLoading = false;
      this.searchHighlight = -1;

      if (updateUrl) {
        history.pushState({ conversationUuid: conv.uuid }, '', `/chat/${conv.uuid}`);
      }

      // Wait for Alpine to render the x-if="activeConversation" template
      // so that #messages-container exists in the DOM
      await this.$nextTick();

      await this.loadMessages(conv.uuid);
      await this.markAsRead(conv.uuid);

      conv.unread_count = 0;

      this.$nextTick(() => {
        this.scrollToBottom();
        this.$refs.messageInput?.focus();
      });
    },

    // ── Messages (server-rendered HTML) ─────────────────────
    _initMessagesDom(container) {
      // Initialize Alpine on dynamically injected HTML and refresh Lucide icons
      if (typeof Alpine !== 'undefined') Alpine.initTree(container);
      if (typeof lucide !== 'undefined') lucide.createIcons({ nodes: [container] });
    },

    async loadMessages(conversationId) {
      this.loadingMessages = true;
      const container = document.getElementById('messages-container');
      if (container) container.innerHTML = '';

      try {
        const resp = await fetch(
          `/chat/${conversationId}/messages`,
          { credentials: 'same-origin' }
        );
        if (resp.ok) {
          const html = await resp.text();
          if (container) {
            container.innerHTML = html;
            this._initMessagesDom(container);
            this._readPaginationState();
          }
        }
      } catch (e) {
        console.error('Failed to load messages', e);
      }
      this.loadingMessages = false;
    },

    _readPaginationState() {
      const list = document.getElementById('message-list');
      if (!list) return;
      this.hasMoreMessages = list.dataset.hasMore === 'true';
    },

    async loadMoreMessages() {
      if (!this.activeConversation || !this.hasMoreMessages || this.loadingMoreMessages) return;

      this.loadingMoreMessages = true;
      const list = document.getElementById('message-list');
      const firstUuid = list?.dataset.firstUuid;
      if (!firstUuid) {
        this.loadingMoreMessages = false;
        return;
      }

      const scrollContainer = this.$refs.messagesContainer;
      const prevScrollHeight = scrollContainer.scrollHeight;

      try {
        const resp = await fetch(
          `/chat/${this.activeConversation.uuid}/messages?before=${firstUuid}`,
          { credentials: 'same-origin' }
        );
        if (resp.ok) {
          const html = await resp.text();
          const parser = new DOMParser();
          const doc = parser.parseFromString(html, 'text/html');
          const newList = doc.getElementById('message-list');

          if (newList && list) {
            // Update pagination data from the new response
            this.hasMoreMessages = newList.dataset.hasMore === 'true';
            list.dataset.hasMore = newList.dataset.hasMore;
            if (newList.dataset.firstUuid) {
              list.dataset.firstUuid = newList.dataset.firstUuid;
            }

            // Prepend new content before existing content
            const fragment = document.createDocumentFragment();
            while (newList.firstChild) {
              fragment.appendChild(newList.firstChild);
            }
            list.insertBefore(fragment, list.firstChild);
            this._initMessagesDom(list);

            // Maintain scroll position
            this.$nextTick(() => {
              scrollContainer.scrollTop = scrollContainer.scrollHeight - prevScrollHeight;
            });
          }
        }
      } catch (e) {
        console.error('Failed to load more messages', e);
      }
      this.loadingMoreMessages = false;
    },

    handleScroll() {
      const container = this.$refs.messagesContainer;
      if (container && container.scrollTop < 50 && this.hasMoreMessages && !this.loadingMoreMessages) {
        this.loadMoreMessages();
      }
    },

    scrollToBottom() {
      const container = this.$refs.messagesContainer;
      if (container) {
        container.scrollTop = container.scrollHeight;
      }
    },

    // ── Sending messages ───────────────────────────────────
    async sendOrEdit() {
      if (this.editingMessageUuid) {
        await this.saveEdit();
      } else {
        await this.sendMessage();
      }
    },

    async sendMessage() {
      const body = this.messageBody.trim();
      const files = [...this.pendingFiles];
      if ((!body && files.length === 0) || !this.activeConversation) return;

      this.messageBody = '';
      // Revoke object URLs before clearing
      for (const f of this.pendingFiles) {
        if (f._preview) URL.revokeObjectURL(f._preview);
      }
      this.pendingFiles = [];

      try {
        let resp;
        if (files.length > 0) {
          const formData = new FormData();
          formData.append('body', body);
          for (const f of files) {
            formData.append('files', f);
          }
          resp = await fetch(
            `/api/v1/chat/conversations/${this.activeConversation.uuid}/messages`,
            {
              method: 'POST',
              headers: { 'X-CSRFToken': this._csrf() },
              credentials: 'same-origin',
              body: formData,
            }
          );
        } else {
          resp = await fetch(
            `/api/v1/chat/conversations/${this.activeConversation.uuid}/messages`,
            {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': this._csrf(),
              },
              credentials: 'same-origin',
              body: JSON.stringify({ body }),
            }
          );
        }

        if (resp.ok) {
          const msg = await resp.json();
          this._updateConversationLastMessage(this.activeConversation.uuid, msg);
          // Re-fetch messages to get proper server-rendered grouping
          await this._refreshCurrentMessages();
          this.$nextTick(() => this.scrollToBottom());
        } else {
          // Restore on error
          this.messageBody = body;
          this.pendingFiles = files;
        }
      } catch (e) {
        console.error('Failed to send message', e);
        this.messageBody = body;
        this.pendingFiles = files;
      }
    },

    async _refreshCurrentMessages() {
      // Reload server-rendered messages for the active conversation
      if (!this.activeConversation) return;
      const container = document.getElementById('messages-container');
      try {
        const resp = await fetch(
          `/chat/${this.activeConversation.uuid}/messages`,
          { credentials: 'same-origin' }
        );
        if (resp.ok) {
          const html = await resp.text();
          if (container) {
            container.innerHTML = html;
            this._initMessagesDom(container);
            this._readPaginationState();
          }
        }
      } catch (e) {
        console.error('Failed to refresh messages', e);
      }
    },

    // ── Editing ────────────────────────────────────────────
    startEdit(msgUuid) {
      const el = document.getElementById(`msg-${msgUuid}`);
      if (!el) return;
      this.editingMessageUuid = msgUuid;
      this.messageBody = el.dataset.body || '';
      this.$nextTick(() => this.$refs.messageInput?.focus());
    },

    cancelEdit() {
      this.editingMessageUuid = null;
      this.messageBody = '';
    },

    async saveEdit() {
      const body = this.messageBody.trim();
      if (!body || !this.editingMessageUuid) return;

      try {
        const resp = await fetch(
          `/api/v1/chat/conversations/${this.activeConversation.uuid}/messages/${this.editingMessageUuid}`,
          {
            method: 'PATCH',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': this._csrf(),
            },
            credentials: 'same-origin',
            body: JSON.stringify({ body }),
          }
        );

        if (resp.ok) {
          const updated = await resp.json();
          // Update the DOM element directly
          const el = document.getElementById(`msg-${updated.uuid}`);
          if (el) {
            const bodyEl = el.querySelector('.msg-body');
            if (bodyEl) bodyEl.innerHTML = updated.body_html;
            // Add edited indicator if not already present
            if (!el.querySelector('.edited-indicator')) {
              const indicator = document.createElement('span');
              indicator.className = 'text-[0.65rem] opacity-50 italic ml-1 edited-indicator';
              indicator.textContent = '(edited)';
              el.appendChild(indicator);
            }
          }
        }
      } catch (e) {
        console.error('Failed to edit message', e);
      }

      this.editingMessageUuid = null;
      this.messageBody = '';
    },

    // ── Deleting ───────────────────────────────────────────
    async deleteMessage(msgUuid) {
      const ok = await AppDialog.confirm({
        title: 'Delete message',
        message: 'Are you sure you want to delete this message?',
        okLabel: 'Delete',
        okClass: 'btn-error',
      });
      if (!ok) return;

      try {
        const resp = await fetch(
          `/api/v1/chat/conversations/${this.activeConversation.uuid}/messages/${msgUuid}`,
          {
            method: 'DELETE',
            headers: { 'X-CSRFToken': this._csrf() },
            credentials: 'same-origin',
          }
        );

        if (resp.ok) {
          // Replace the message bubble with a "deleted" placeholder
          const el = document.getElementById(`msg-${msgUuid}`);
          if (el) {
            // Replace the entire row (parent .group/msg div) with a simple deleted indicator
            const row = el.closest('.group\\/msg') || el.parentElement;
            const placeholder = document.createElement('div');
            placeholder.className = 'msg-bubble rounded-2xl px-3 py-1.5 text-sm italic bg-base-200 text-base-content/40';
            placeholder.id = `msg-${msgUuid}`;
            placeholder.textContent = 'Message deleted';
            row.replaceWith(placeholder);
          }
        }
      } catch (e) {
        console.error('Failed to delete message', e);
      }
    },

    // ── Reactions ──────────────────────────────────────────
    async toggleReaction(messageId, emoji) {
      try {
        const resp = await fetch(
          `/api/v1/chat/messages/${messageId}/reactions`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': this._csrf(),
            },
            credentials: 'same-origin',
            body: JSON.stringify({ emoji }),
          }
        );

        if (resp.ok) {
          // Re-fetch to get server-rendered reactions with proper grouping
          await this._refreshCurrentMessages();
        }
      } catch (e) {
        console.error('Failed to toggle reaction', e);
      }
    },

    // ── Read status ────────────────────────────────────────
    async markAsRead(conversationId) {
      try {
        await fetch(`/api/v1/chat/conversations/${conversationId}/read`, {
          method: 'POST',
          headers: { 'X-CSRFToken': this._csrf() },
          credentials: 'same-origin',
        });
      } catch (e) {
        console.error('Failed to mark as read', e);
      }
    },

    // ── New conversation ───────────────────────────────────
    showNewConversationDialog() {
      this.selectedUsers = [];
      this.newConvTitle = '';
      this.userSearchQuery = '';
      this.userSearchResults = [];
      this.userSearchShowDropdown = false;
      this.$refs.newConvDialog.showModal();
      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') lucide.createIcons();
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
      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') lucide.createIcons();
      });
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
            'X-CSRFToken': this._csrf(),
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

    // ── SSE event handlers ─────────────────────────────────
    async handleSSEMessage(detail) {
      if (this.activeConversation && detail.conversation_id === this.activeConversation.uuid) {
        // Check if message already exists in the DOM
        if (!document.getElementById(`msg-${detail.message.uuid}`)) {
          await this._refreshCurrentMessages();
          this.scrollToBottom();
          this.markAsRead(detail.conversation_id);
        }
      }

      this._updateConversationLastMessage(detail.conversation_id, detail.message);
      this._bumpConversationUnread(detail.conversation_id);
      this.refreshConversationList();
    },

    handleSSEMessageEdited(detail) {
      if (this.activeConversation && detail.conversation_id === this.activeConversation.uuid) {
        const el = document.getElementById(`msg-${detail.message_id}`);
        if (el) {
          const bodyEl = el.querySelector('.msg-body');
          if (bodyEl) bodyEl.innerHTML = detail.body_html;
          if (!el.querySelector('.edited-indicator')) {
            const indicator = document.createElement('span');
            indicator.className = 'text-[0.65rem] opacity-50 italic ml-1 edited-indicator';
            indicator.textContent = '(edited)';
            el.appendChild(indicator);
          }
          el.dataset.body = detail.body;
        }
      }
    },

    handleSSEMessageDeleted(detail) {
      if (this.activeConversation && detail.conversation_id === this.activeConversation.uuid) {
        // Re-fetch to get proper grouping after deletion
        this._refreshCurrentMessages();
      }
    },

    async handleSSEReaction(detail) {
      if (this.activeConversation && detail.conversation_id === this.activeConversation.uuid) {
        await this._refreshCurrentMessages();
      }
    },

    handleSSEUnread(detail) {
      if (typeof Alpine !== 'undefined' && Alpine.store('chat')) {
        Alpine.store('chat').totalUnread = detail.total;
        Alpine.store('chat').conversationUnreads = detail.conversations;
      }

      for (const conv of this.conversations) {
        const count = detail.conversations[conv.uuid] || 0;
        if (this.activeConversation && conv.uuid === this.activeConversation.uuid) {
          conv.unread_count = 0;
        } else {
          conv.unread_count = count;
        }
      }
    },

    // ── Helpers ─────────────────────────────────────────────
    _updateConversationLastMessage(convId, msg) {
      const conv = this.conversations.find(c => c.uuid === convId);
      if (conv) {
        conv.last_message = {
          uuid: msg.uuid,
          author: msg.author,
          body: msg.body,
          created_at: msg.created_at,
          has_attachments: msg.has_attachments || (msg.attachments && msg.attachments.length > 0),
        };
        conv.updated_at = msg.created_at;
      }
      this.conversations.sort((a, b) => {
        // Pinned conversations always come first, sorted by pin_position
        if (a.is_pinned && !b.is_pinned) return -1;
        if (!a.is_pinned && b.is_pinned) return 1;
        if (a.is_pinned && b.is_pinned) return (a.pin_position || 0) - (b.pin_position || 0);
        return new Date(b.updated_at) - new Date(a.updated_at);
      });
    },

    _bumpConversationUnread(convId) {
      if (this.activeConversation && this.activeConversation.uuid === convId) return;
      const conv = this.conversations.find(c => c.uuid === convId);
      if (conv) {
        conv.unread_count = (conv.unread_count || 0) + 1;
      }
    },

    conversationName(conv) {
      if (conv.title) return conv.title;
      if (conv.kind === 'dm') {
        const other = conv.members?.find(m => m.user.id !== this.currentUserId);
        return other ? other.user.username : 'Direct Message';
      }
      const names = (conv.members || [])
        .filter(m => m.user.id !== this.currentUserId)
        .map(m => m.user.username)
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
      if (conv.avatar_url) {
        return `<div class="w-10 h-10 rounded-full overflow-hidden flex-shrink-0"><img src="${conv.avatar_url}" alt="Group avatar" class="w-full h-full object-cover" /></div>`;
      }
      const initials = (conv.members || [])
        .filter(m => m.user.id !== this.currentUserId)
        .slice(0, 2)
        .map(m => m.user.username[0].toUpperCase())
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

    toggleInfoPanel() {
      this.showInfoPanel = !this.showInfoPanel;
      if (this.showInfoPanel) {
        this.closeSearchPanel();
        this.$nextTick(() => {
          if (typeof lucide !== 'undefined') lucide.createIcons();
        });
        if (this.activeConversation) {
          this.loadConversationStats(this.activeConversation.uuid);
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

    // ── Search panel ────────────────────────────────────────
    toggleSearchPanel() {
      this.showSearchPanel = !this.showSearchPanel;
      if (this.showSearchPanel) {
        this.showInfoPanel = false;
        this.$nextTick(() => {
          this.$refs.searchInput?.focus();
          if (typeof lucide !== 'undefined') lucide.createIcons();
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
    },

    async searchMessages() {
      const q = (this.searchQuery || '').trim();
      if (q.length < 2) {
        this.searchResults = [];
        return;
      }
      if (!this.activeConversation) return;

      this.searchLoading = true;
      try {
        const resp = await fetch(
          `/api/v1/chat/conversations/${this.activeConversation.uuid}/messages/search?q=${encodeURIComponent(q)}`,
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

    // ── Conversation management ──────────────────────────────
    async renameConversation() {
      if (!this.activeConversation || this.activeConversation.kind !== 'group') return;
      const current = this.activeConversation.title || '';
      const title = await AppDialog.prompt({
        title: 'Rename group',
        message: 'Enter a new name for this group:',
        value: current,
        placeholder: 'Group name',
        okLabel: 'Rename',
      });
      if (title === null) return;
      const trimmed = title.trim();
      if (!trimmed || trimmed === current) return;

      try {
        const resp = await fetch(`/api/v1/chat/conversations/${this.activeConversation.uuid}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this._csrf() },
          credentials: 'same-origin',
          body: JSON.stringify({ title: trimmed }),
        });
        if (resp.ok) {
          this.activeConversation.title = trimmed;
          const conv = this.conversations.find(c => c.uuid === this.activeConversation.uuid);
          if (conv) conv.title = trimmed;
          this.refreshConversationList();
        }
      } catch (e) {
        console.error('Failed to rename conversation', e);
      }
    },

    async leaveConversation() {
      if (!this.activeConversation) return;
      const ok = await AppDialog.confirm({
        title: 'Leave conversation',
        message: 'Are you sure you want to leave this conversation?',
        okLabel: 'Leave',
        okClass: 'btn-error',
      });
      if (!ok) return;

      try {
        const resp = await fetch(`/api/v1/chat/conversations/${this.activeConversation.uuid}`, {
          method: 'DELETE',
          headers: { 'X-CSRFToken': this._csrf() },
          credentials: 'same-origin',
        });
        if (resp.ok || resp.status === 204) {
          this.conversations = this.conversations.filter(c => c.uuid !== this.activeConversation.uuid);
          this.activeConversation = null;
          this.showInfoPanel = false;
          history.pushState({}, '', '/chat');
          this.refreshConversationList();
        }
      } catch (e) {
        console.error('Failed to leave conversation', e);
      }
    },

    async addMembersToConversation() {
      if (!this.activeConversation || this.activeConversation.kind !== 'group') return;
      this.addMemberSelected = [];
      this.addMemberSearchQuery = '';
      this.addMemberResults = [];
      this.addMemberShowDropdown = false;
      this.addMemberHighlight = -1;
      this.$refs.addMemberDialog.showModal();
      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') lucide.createIcons();
        this.$refs.addMemberSearchInput?.focus();
      });
    },

    async searchUsersForAdd() {
      const q = (this.addMemberSearchQuery || '').trim();
      if (q.length < 2) {
        this.addMemberResults = [];
        this.addMemberShowDropdown = false;
        return;
      }
      this.addMemberLoading = true;
      try {
        const resp = await fetch(`/api/v1/users/search?q=${encodeURIComponent(q)}&limit=10`, {
          credentials: 'same-origin',
        });
        if (resp.ok) {
          const data = await resp.json();
          const existingIds = new Set((this.activeConversation.members || []).map(m => m.user.id));
          const selectedIds = new Set(this.addMemberSelected.map(u => u.id));
          this.addMemberResults = (data.results || []).filter(
            u => u.id !== this.currentUserId && !existingIds.has(u.id) && !selectedIds.has(u.id)
          );
          this.addMemberHighlight = -1;
          this.addMemberShowDropdown = true;
        }
      } catch (e) {
        console.error('User search failed', e);
      }
      this.addMemberLoading = false;
    },

    handleAddMemberKeydown(e) {
      const results = this.addMemberResults;
      const dropdownOpen = this.addMemberShowDropdown && results.length > 0;

      if (e.key === 'ArrowDown' && dropdownOpen) {
        e.preventDefault();
        this.addMemberHighlight = (this.addMemberHighlight + 1) % results.length;
      } else if (e.key === 'ArrowUp' && dropdownOpen) {
        e.preventDefault();
        this.addMemberHighlight = this.addMemberHighlight <= 0 ? results.length - 1 : this.addMemberHighlight - 1;
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (dropdownOpen && this.addMemberHighlight >= 0 && this.addMemberHighlight < results.length) {
          this.selectAddMember(results[this.addMemberHighlight]);
          this.$refs.addMemberSearchInput?.focus();
        } else if (this.addMemberSelected.length > 0 && !this.addMemberSearchQuery?.trim()) {
          this.confirmAddMembers();
        }
      }
    },

    selectAddMember(user) {
      if (!this.addMemberSelected.find(u => u.id === user.id)) {
        this.addMemberSelected.push(user);
      }
      this.addMemberSearchQuery = '';
      this.addMemberResults = [];
      this.addMemberHighlight = -1;
      this.addMemberShowDropdown = false;
      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') lucide.createIcons();
      });
    },

    removeAddMember(userId) {
      this.addMemberSelected = this.addMemberSelected.filter(u => u.id !== userId);
    },

    async confirmAddMembers() {
      if (!this.addMemberSelected.length || !this.activeConversation) return;
      this.addMemberSaving = true;
      try {
        const resp = await fetch(`/api/v1/chat/conversations/${this.activeConversation.uuid}/members`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this._csrf() },
          credentials: 'same-origin',
          body: JSON.stringify({ user_ids: this.addMemberSelected.map(u => u.id) }),
        });
        if (resp.ok) {
          const updated = await resp.json();
          this.activeConversation.members = updated.members;
          const conv = this.conversations.find(c => c.uuid === this.activeConversation.uuid);
          if (conv) conv.members = updated.members;
          this.$refs.addMemberDialog.close();
          this.refreshConversationList();
          this.$nextTick(() => {
            if (typeof lucide !== 'undefined') lucide.createIcons();
          });
        }
      } catch (e) {
        console.error('Failed to add members', e);
      }
      this.addMemberSaving = false;
    },

    async removeMember(userId) {
      if (!this.activeConversation) return;
      const member = this.activeConversation.members?.find(m => m.user.id === userId);
      const name = member ? member.user.username : 'this member';
      const ok = await AppDialog.confirm({
        title: 'Remove member',
        message: `Remove ${name} from this group?`,
        okLabel: 'Remove',
        okClass: 'btn-error',
      });
      if (!ok) return;

      try {
        const resp = await fetch(`/api/v1/chat/conversations/${this.activeConversation.uuid}/members/${userId}`, {
          method: 'DELETE',
          headers: { 'X-CSRFToken': this._csrf() },
          credentials: 'same-origin',
        });
        if (resp.ok || resp.status === 204) {
          this.activeConversation.members = this.activeConversation.members.filter(m => m.user.id !== userId);
          const conv = this.conversations.find(c => c.uuid === this.activeConversation.uuid);
          if (conv) conv.members = this.activeConversation.members;
          this.refreshConversationList();
          this.$nextTick(() => {
            if (typeof lucide !== 'undefined') lucide.createIcons();
          });
        }
      } catch (e) {
        console.error('Failed to remove member', e);
      }
    },

    // ── Group avatar ──────────────────────────────────────────
    _cropper: null,
    _cropFile: null,
    _cropUploading: false,

    uploadGroupAvatar(fileInput) {
      if (!this.activeConversation || this.activeConversation.kind !== 'group') return;
      const file = fileInput.files?.[0];
      if (!file) return;
      this._cropFile = file;

      const reader = new FileReader();
      reader.onload = (e) => {
        this.$refs.cropperImage.src = e.target.result;
        this.$refs.cropperDialog.showModal();

        this.$nextTick(() => {
          if (this._cropper) {
            this._cropper.destroy();
          }
          this._cropper = new Cropper(this.$refs.cropperImage, {
            aspectRatio: 1,
            viewMode: 1,
            movable: true,
            zoomable: true,
            rotatable: false,
            scalable: false,
            guides: true,
            center: true,
            highlight: false,
            background: true,
          });
        });
      };
      reader.readAsDataURL(file);
      fileInput.value = '';
    },

    async confirmAvatarCrop() {
      if (!this._cropper || !this._cropFile || !this.activeConversation) return;
      this._cropUploading = true;

      const data = this._cropper.getData(true);
      const formData = new FormData();
      formData.append('image', this._cropFile);
      formData.append('crop_x', data.x);
      formData.append('crop_y', data.y);
      formData.append('crop_w', data.width);
      formData.append('crop_h', data.height);

      try {
        const resp = await fetch(`/api/v1/chat/conversations/${this.activeConversation.uuid}/avatar`, {
          method: 'POST',
          headers: { 'X-CSRFToken': this._csrf() },
          credentials: 'same-origin',
          body: formData,
        });
        if (resp.ok) {
          const avatarUrl = `/api/v1/chat/conversations/${this.activeConversation.uuid}/avatar/image`;
          const bustUrl = `${avatarUrl}?t=${Date.now()}`;
          this.activeConversation.avatar_url = bustUrl;
          const conv = this.conversations.find(c => c.uuid === this.activeConversation.uuid);
          if (conv) conv.avatar_url = bustUrl;
          this.refreshConversationList();
        }
      } catch (e) {
        console.error('Failed to upload group avatar', e);
      } finally {
        this._cropUploading = false;
        this.$refs.cropperDialog.close();
        if (this._cropper) {
          this._cropper.destroy();
          this._cropper = null;
        }
        this._cropFile = null;
      }
    },

    cancelAvatarCrop() {
      this.$refs.cropperDialog.close();
      if (this._cropper) {
        this._cropper.destroy();
        this._cropper = null;
      }
      this._cropFile = null;
    },

    async removeGroupAvatar() {
      if (!this.activeConversation || this.activeConversation.kind !== 'group') return;

      const ok = await AppDialog.confirm({
        title: 'Remove avatar',
        message: 'Remove the group avatar?',
        okLabel: 'Remove',
        okClass: 'btn-error',
      });
      if (!ok) return;

      try {
        const resp = await fetch(`/api/v1/chat/conversations/${this.activeConversation.uuid}/avatar`, {
          method: 'DELETE',
          headers: { 'X-CSRFToken': this._csrf() },
          credentials: 'same-origin',
        });
        if (resp.ok || resp.status === 200) {
          this.activeConversation.avatar_url = null;
          const conv = this.conversations.find(c => c.uuid === this.activeConversation.uuid);
          if (conv) conv.avatar_url = null;
          this.refreshConversationList();
        }
      } catch (e) {
        console.error('Failed to remove group avatar', e);
      }
    },

    copyConversationLink(uuid) {
      const id = uuid || this.activeConversation?.uuid;
      if (!id) return;
      const url = `${window.location.origin}/chat/${id}`;
      navigator.clipboard.writeText(url).then(() => {
        this._linkCopied = true;
        setTimeout(() => { this._linkCopied = false; }, 2000);
      });
    },

    // ── Pinning ─────────────────────────────────────────────
    async pinConversation(uuid) {
      try {
        const resp = await fetch(`/api/v1/chat/conversations/${uuid}/pin`, {
          method: 'POST',
          headers: { 'X-CSRFToken': this._csrf() },
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
          headers: { 'X-CSRFToken': this._csrf() },
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
            'X-CSRFToken': this._csrf(),
          },
          credentials: 'same-origin',
          body: JSON.stringify({ order }),
        });
        this.refreshConversationList();
      } catch (e) {
        console.error('Failed to reorder pinned conversations', e);
      }
    },

    // ── Context menu ─────────────────────────────────────────
    openConvContextMenu(event, uuid, kind, isPinned = false) {
      event.preventDefault();
      const menu = document.getElementById('conv-context-menu');
      if (!menu) return;

      this.ctxMenu.uuid = uuid;
      this.ctxMenu.kind = kind;
      this.ctxMenu.isPinned = isPinned || this.conversations.find(c => c.uuid === uuid)?.is_pinned || false;
      this.ctxMenu.open = true;

      this.$nextTick(() => {
        const rect = menu.getBoundingClientRect();
        let x = event.clientX;
        let y = event.clientY;
        if (x + rect.width > window.innerWidth) x = window.innerWidth - rect.width - 10;
        if (y + rect.height > window.innerHeight) y = window.innerHeight - rect.height - 10;
        this.ctxMenu.x = x;
        this.ctxMenu.y = y;
        if (typeof lucide !== 'undefined') lucide.createIcons({ nodes: [menu] });
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
          this.$nextTick(() => {
            if (typeof lucide !== 'undefined') lucide.createIcons();
          });
          if (this.activeConversation) {
            this.loadConversationStats(this.activeConversation.uuid);
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

    formatDate(iso) {
      if (!iso) return '';
      const d = new Date(iso);
      return d.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
    },

    formatDateTime(iso) {
      if (!iso) return '';
      const d = new Date(iso);
      return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    },

    memberDisplayName(member) {
      const u = member.user;
      const full = ((u.first_name || '') + ' ' + (u.last_name || '')).trim();
      return full || u.username;
    },

    autoResize(el) {
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 128) + 'px';
    },

    insertEmoji(emoji) {
      const ta = this.$refs.messageInput;
      if (!ta) {
        this.messageBody += emoji;
        return;
      }
      const start = ta.selectionStart;
      const end = ta.selectionEnd;
      this.messageBody = this.messageBody.slice(0, start) + emoji + this.messageBody.slice(end);
      this.$nextTick(() => {
        const pos = start + emoji.length;
        ta.setSelectionRange(pos, pos);
        ta.focus();
      });
    },

    // ── Input keyboard shortcuts ────────────────────────────
    handleInputKeydown(e) {
      const ta = this.$refs.messageInput;

      // Enter (without shift) → send / save edit
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.sendOrEdit();
        return;
      }

      // Escape → cancel edit or blur
      if (e.key === 'Escape') {
        if (this.editingMessageUuid) {
          this.cancelEdit();
        } else {
          ta?.blur();
        }
        return;
      }

      // Arrow Up when input is empty → edit last own message
      if (e.key === 'ArrowUp' && !this.messageBody) {
        this.editLastOwnMessage();
        return;
      }

      const isMod = e.ctrlKey || e.metaKey;

      // Ctrl/Cmd+B → bold
      if (isMod && e.key === 'b') {
        e.preventDefault();
        this.wrapSelection('**');
        return;
      }

      // Ctrl/Cmd+I → italic
      if (isMod && e.key === 'i') {
        e.preventDefault();
        this.wrapSelection('*');
        return;
      }

      // Ctrl/Cmd+E → inline code
      if (isMod && e.key === 'e') {
        e.preventDefault();
        this.wrapSelection('`');
        return;
      }

      // Ctrl/Cmd+Shift+X → strikethrough
      if (isMod && e.shiftKey && e.key === 'X') {
        e.preventDefault();
        this.wrapSelection('~~');
        return;
      }
    },

    wrapSelection(marker) {
      const ta = this.$refs.messageInput;
      if (!ta) return;
      ta.focus();

      const start = ta.selectionStart;
      const end = ta.selectionEnd;
      const text = this.messageBody;
      const selected = text.slice(start, end);

      if (selected) {
        // Wrap selected text
        const wrapped = marker + selected + marker;
        this.messageBody = text.slice(0, start) + wrapped + text.slice(end);
        this.$nextTick(() => {
          ta.setSelectionRange(start + marker.length, end + marker.length);
          ta.focus();
        });
      } else {
        // Insert empty markers with cursor between them
        this.messageBody = text.slice(0, start) + marker + marker + text.slice(end);
        this.$nextTick(() => {
          const pos = start + marker.length;
          ta.setSelectionRange(pos, pos);
          ta.focus();
        });
      }
    },

    insertLink() {
      const ta = this.$refs.messageInput;
      if (!ta) return;
      ta.focus();

      const start = ta.selectionStart;
      const end = ta.selectionEnd;
      const text = this.messageBody;
      const selected = text.slice(start, end);

      if (selected) {
        // Use selected text as the link text
        const link = `[${selected}](url)`;
        this.messageBody = text.slice(0, start) + link + text.slice(end);
        this.$nextTick(() => {
          // Select "url" for quick replacement
          const urlStart = start + selected.length + 3; // [text](
          const urlEnd = urlStart + 3; // url
          ta.setSelectionRange(urlStart, urlEnd);
          ta.focus();
        });
      } else {
        // Insert template and select "text"
        const link = '[text](url)';
        this.messageBody = text.slice(0, start) + link + text.slice(end);
        this.$nextTick(() => {
          // Select "text" for quick replacement
          ta.setSelectionRange(start + 1, start + 5);
          ta.focus();
        });
      }
    },

    // ── File upload ──────────────────────────────────────────
    openFileDialog() {
      this.$refs.fileInput?.click();
    },

    handleFileSelect(e) {
      const files = e.target.files;
      if (files?.length) this.addFiles(files);
      e.target.value = '';
    },

    addFiles(fileList) {
      const existing = new Set(this.pendingFiles.map(f => f.name + f.size));
      for (const f of fileList) {
        if (existing.has(f.name + f.size)) continue;
        // Generate preview URL for images
        if (f.type.startsWith('image/')) {
          f._preview = URL.createObjectURL(f);
        }
        this.pendingFiles.push(f);
      }
      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') lucide.createIcons();
      });
    },

    removeFile(idx) {
      const file = this.pendingFiles[idx];
      if (file?._preview) URL.revokeObjectURL(file._preview);
      this.pendingFiles.splice(idx, 1);
    },

    formatFileSize(bytes) {
      if (!bytes) return '0 B';
      const units = ['B', 'KB', 'MB', 'GB'];
      let val = bytes;
      for (const unit of units) {
        if (val < 1024) return unit === 'B' ? `${val} B` : `${val.toFixed(1)} ${unit}`;
        val /= 1024;
      }
      return `${val.toFixed(1)} TB`;
    },

    isImageFile(file) {
      return file.type?.startsWith('image/');
    },

    handleDragEnter(e) {
      if (!e.dataTransfer?.types?.includes('Files')) return;
      this._dragCounter++;
      this.isDraggingOver = true;
    },

    handleDragOver(e) {
      e.dataTransfer.dropEffect = 'copy';
    },

    handleDragLeave(e) {
      this._dragCounter--;
      if (this._dragCounter <= 0) {
        this._dragCounter = 0;
        this.isDraggingOver = false;
      }
    },

    handleDrop(e) {
      this._dragCounter = 0;
      this.isDraggingOver = false;
      const files = e.dataTransfer?.files;
      if (files?.length) this.addFiles(files);
    },

    handlePaste(e) {
      const items = e.clipboardData?.items;
      if (!items) return;
      const files = [];
      for (const item of items) {
        if (item.kind === 'file') {
          const f = item.getAsFile();
          if (f) files.push(f);
        }
      }
      if (files.length > 0) {
        e.preventDefault();
        this.addFiles(files);
      }
    },

    editLastOwnMessage() {
      // Find the last message bubble authored by the current user
      const container = document.getElementById('messages-container');
      if (!container) return;

      const bubbles = container.querySelectorAll('.msg-bubble[data-body]');
      // Walk backwards to find the last one from current user
      for (let i = bubbles.length - 1; i >= 0; i--) {
        const bubble = bubbles[i];
        // msg-group-end marks own messages (chat-end / right-aligned)
        if (bubble.closest('.msg-group-end')) {
          const msgId = bubble.id?.replace('msg-', '');
          if (msgId) {
            this.startEdit(msgId);
            return;
          }
        }
      }
    },
  };
}
