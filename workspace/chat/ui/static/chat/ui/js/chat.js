function chatApp(currentUserId) {
  return {
    // ── State ──────────────────────────────────────────────
    conversations: [],
    activeConversation: null,
    messages: [],
    messageBody: '',
    loadingMessages: false,
    loadingMoreMessages: false,
    hasMoreMessages: false,
    editingMessage: null,
    selectedUsers: [],
    newConvTitle: '',
    creatingConversation: false,
    userSearchQuery: '',
    userSearchResults: [],
    userSearchLoading: false,
    userSearchShowDropdown: false,
    currentUserId: currentUserId,
    quickEmojis: ['\ud83d\udc4d', '\u2764\ufe0f', '\ud83d\ude02', '\ud83d\ude2e', '\ud83d\ude22', '\ud83c\udf89'],

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
      // Refresh the server-rendered sidebar list
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
      // Also refresh local data
      await this.loadConversations();
    },

    async selectConversationById(uuid) {
      // Try local state first
      let conv = this.conversations.find(c => c.uuid === uuid);
      if (!conv) {
        // Fetch from API
        try {
          const resp = await fetch(`/api/v1/chat/conversations/${uuid}`, { credentials: 'same-origin' });
          if (!resp.ok) return;
          conv = await resp.json();
        } catch (e) {
          console.error('Failed to load conversation', e);
          return;
        }
      }
      await this.selectConversation(conv);
    },

    async selectConversation(conv) {
      this.activeConversation = conv;
      this.messages = [];
      this.hasMoreMessages = false;
      this.editingMessage = null;
      this.messageBody = '';

      await this.loadMessages(conv.uuid);
      await this.markAsRead(conv.uuid);

      // Update unread count locally
      conv.unread_count = 0;

      this.$nextTick(() => {
        this.scrollToBottom();
        this.$refs.messageInput?.focus();
        if (typeof lucide !== 'undefined') lucide.createIcons();
      });
    },

    // ── Messages ───────────────────────────────────────────
    async loadMessages(conversationId) {
      this.loadingMessages = true;
      try {
        const resp = await fetch(
          `/api/v1/chat/conversations/${conversationId}/messages?limit=50`,
          { credentials: 'same-origin' }
        );
        if (resp.ok) {
          const data = await resp.json();
          this.messages = data.messages;
          this.hasMoreMessages = data.has_more;
        }
      } catch (e) {
        console.error('Failed to load messages', e);
      }
      this.loadingMessages = false;
      this.$nextTick(() => {
        if (typeof lucide !== 'undefined') lucide.createIcons();
      });
    },

    async loadMoreMessages() {
      if (!this.activeConversation || !this.hasMoreMessages || this.loadingMoreMessages) return;

      this.loadingMoreMessages = true;
      const firstMsg = this.messages[0];
      if (!firstMsg) {
        this.loadingMoreMessages = false;
        return;
      }

      const container = this.$refs.messagesContainer;
      const prevScrollHeight = container.scrollHeight;

      try {
        const resp = await fetch(
          `/api/v1/chat/conversations/${this.activeConversation.uuid}/messages?before=${firstMsg.uuid}&limit=50`,
          { credentials: 'same-origin' }
        );
        if (resp.ok) {
          const data = await resp.json();
          this.messages = [...data.messages, ...this.messages];
          this.hasMoreMessages = data.has_more;

          // Maintain scroll position
          this.$nextTick(() => {
            container.scrollTop = container.scrollHeight - prevScrollHeight;
            if (typeof lucide !== 'undefined') lucide.createIcons();
          });
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
      if (this.editingMessage) {
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
          this.messages.push(msg);
          this._updateConversationLastMessage(this.activeConversation.uuid, msg);
          this.$nextTick(() => {
            this.scrollToBottom();
            if (typeof lucide !== 'undefined') lucide.createIcons();
          });
        }
      } catch (e) {
        console.error('Failed to send message', e);
        this.messageBody = body; // Restore
      }
    },

    // ── Editing ────────────────────────────────────────────
    startEdit(msg) {
      this.editingMessage = msg;
      this.messageBody = msg.body;
      this.$nextTick(() => this.$refs.messageInput?.focus());
    },

    cancelEdit() {
      this.editingMessage = null;
      this.messageBody = '';
    },

    async saveEdit() {
      const body = this.messageBody.trim();
      if (!body || !this.editingMessage) return;

      try {
        const resp = await fetch(
          `/api/v1/chat/conversations/${this.activeConversation.uuid}/messages/${this.editingMessage.uuid}`,
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
          const idx = this.messages.findIndex(m => m.uuid === updated.uuid);
          if (idx !== -1) {
            this.messages[idx] = updated;
          }
        }
      } catch (e) {
        console.error('Failed to edit message', e);
      }

      this.editingMessage = null;
      this.messageBody = '';
    },

    // ── Deleting ───────────────────────────────────────────
    async deleteMessage(msg) {
      const ok = await AppDialog.confirm({
        title: 'Delete message',
        message: 'Are you sure you want to delete this message?',
        okLabel: 'Delete',
        okClass: 'btn-error',
      });
      if (!ok) return;

      try {
        const resp = await fetch(
          `/api/v1/chat/conversations/${this.activeConversation.uuid}/messages/${msg.uuid}`,
          {
            method: 'DELETE',
            headers: { 'X-CSRFToken': this._csrf() },
            credentials: 'same-origin',
          }
        );

        if (resp.ok) {
          const idx = this.messages.findIndex(m => m.uuid === msg.uuid);
          if (idx !== -1) {
            this.messages[idx].deleted_at = new Date().toISOString();
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
          const data = await resp.json();
          const msg = this.messages.find(m => m.uuid === messageId);
          if (!msg) return;

          if (data.action === 'added') {
            msg.reactions.push({
              uuid: crypto.randomUUID(),
              emoji,
              user: { id: this.currentUserId, username: 'You' },
              created_at: new Date().toISOString(),
            });
          } else {
            msg.reactions = msg.reactions.filter(
              r => !(r.emoji === emoji && r.user.id === this.currentUserId)
            );
          }
        }
      } catch (e) {
        console.error('Failed to toggle reaction', e);
      }
    },

    groupReactions(msg) {
      if (!msg.reactions || msg.reactions.length === 0) return [];

      const groups = {};
      for (const r of msg.reactions) {
        if (!groups[r.emoji]) {
          groups[r.emoji] = { emoji: r.emoji, count: 0, users: [], hasCurrentUser: false };
        }
        groups[r.emoji].count++;
        groups[r.emoji].users.push(r.user.username);
        if (r.user.id === this.currentUserId) {
          groups[r.emoji].hasCurrentUser = true;
        }
      }
      return Object.values(groups);
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
      this.userSearchShowDropdown = false;
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

          // Refresh both local data and server-rendered list
          await this.refreshConversationList();

          // Select the newly created conversation
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
    handleSSEMessage(detail) {
      if (this.activeConversation && detail.conversation_id === this.activeConversation.uuid) {
        if (!this.messages.find(m => m.uuid === detail.message.uuid)) {
          this.messages.push(detail.message);
          this.$nextTick(() => {
            this.scrollToBottom();
            if (typeof lucide !== 'undefined') lucide.createIcons();
          });
          this.markAsRead(detail.conversation_id);
        }
      }

      // Update conversation list
      this._updateConversationLastMessage(detail.conversation_id, detail.message);
      this._bumpConversationUnread(detail.conversation_id);

      // Refresh the server-rendered sidebar
      this.refreshConversationList();
    },

    handleSSEMessageEdited(detail) {
      if (this.activeConversation && detail.conversation_id === this.activeConversation.uuid) {
        const msg = this.messages.find(m => m.uuid === detail.message_id);
        if (msg) {
          msg.body = detail.body;
          msg.body_html = detail.body_html;
          msg.edited_at = detail.edited_at;
        }
      }
    },

    handleSSEMessageDeleted(detail) {
      if (this.activeConversation && detail.conversation_id === this.activeConversation.uuid) {
        const msg = this.messages.find(m => m.uuid === detail.message_id);
        if (msg) {
          msg.deleted_at = new Date().toISOString();
        }
      }
    },

    handleSSEReaction(detail) {
      if (this.activeConversation && detail.conversation_id === this.activeConversation.uuid) {
        const msg = this.messages.find(m => m.uuid === detail.message_id);
        if (msg) {
          if (detail.action === 'added') {
            msg.reactions.push({
              uuid: crypto.randomUUID(),
              emoji: detail.emoji,
              user: detail.user,
              created_at: new Date().toISOString(),
            });
          } else {
            msg.reactions = msg.reactions.filter(
              r => !(r.emoji === detail.emoji && r.user.id === detail.user.id)
            );
          }
        }
      }
    },

    handleSSEUnread(detail) {
      // Update global store
      if (typeof Alpine !== 'undefined' && Alpine.store('chat')) {
        Alpine.store('chat').totalUnread = detail.total;
        Alpine.store('chat').conversationUnreads = detail.conversations;
      }

      // Update conversation list badges
      for (const conv of this.conversations) {
        const count = detail.conversations[conv.uuid] || 0;
        if (this.activeConversation && conv.uuid === this.activeConversation.uuid) {
          conv.unread_count = 0;
        } else {
          conv.unread_count = count;
        }
      }

      // Refresh server-rendered sidebar for unread badges
      this.refreshConversationList();
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
      // Re-sort conversations
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

    truncate(text, maxLen) {
      if (!text) return '';
      return text.length > maxLen ? text.slice(0, maxLen) + '\u2026' : text;
    },

    timeAgo(dateStr) {
      if (!dateStr) return '';
      const date = new Date(dateStr);
      const now = new Date();
      const diff = Math.floor((now - date) / 1000);

      if (diff < 60) return 'now';
      if (diff < 3600) return Math.floor(diff / 60) + 'm';
      if (diff < 86400) return Math.floor(diff / 3600) + 'h';
      if (diff < 604800) return Math.floor(diff / 86400) + 'd';
      return date.toLocaleDateString();
    },

    formatTime(dateStr) {
      if (!dateStr) return '';
      const date = new Date(dateStr);
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    },

    autoResize(el) {
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 128) + 'px';
    },

    insertEmoji(emoji) {
      this.messageBody += emoji;
      this.$nextTick(() => this.$refs.messageInput?.focus());
    },
  };
}
