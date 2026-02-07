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
      this.showInfoPanel = false;

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
      if (!body || !this.activeConversation) return;

      this.messageBody = '';

      try {
        const resp = await fetch(
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

        if (resp.ok) {
          const msg = await resp.json();
          this._updateConversationLastMessage(this.activeConversation.uuid, msg);
          // Re-fetch messages to get proper server-rendered grouping
          await this._refreshCurrentMessages();
          this.$nextTick(() => this.scrollToBottom());
        }
      } catch (e) {
        console.error('Failed to send message', e);
        this.messageBody = body; // Restore
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
      } catch (e) {}
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

          await this.refreshConversationList();

          const found = this.conversations.find(c => c.uuid === conv.uuid);
          if (found) {
            await this.selectConversation(found);
          }
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
        };
        conv.updated_at = msg.created_at;
      }
      this.conversations.sort((a, b) => {
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

    conversationAvatar(conv) {
      if (conv.kind === 'dm') {
        const other = conv.members?.find(m => m.user.id !== this.currentUserId);
        const name = other ? other.user.username : '?';
        const initial = name[0].toUpperCase();
        return `<div class="avatar placeholder"><div class="w-10 h-10 rounded-full bg-neutral text-neutral-content"><span class="text-sm">${initial}</span></div></div>`;
      }
      const initials = (conv.members || [])
        .filter(m => m.user.id !== this.currentUserId)
        .slice(0, 2)
        .map(m => m.user.username[0].toUpperCase())
        .join('');
      return `<div class="avatar placeholder"><div class="w-10 h-10 rounded-full bg-info text-info-content"><span class="text-sm">${initials || 'G'}</span></div></div>`;
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
        this.$nextTick(() => {
          if (typeof lucide !== 'undefined') lucide.createIcons();
        });
      }
    },

    formatDate(iso) {
      if (!iso) return '';
      const d = new Date(iso);
      return d.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
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
