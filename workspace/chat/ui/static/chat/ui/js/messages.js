// Message lifecycle: load, paginate, send (with optimistic UI), edit,
// delete, reply, reactions, mark-as-read, scroll, pin / unpin messages,
// "edit last own message" shortcut.
window.chatMessagesMixin = function chatMessagesMixin() {
  return {
    // ── State ────────────────────────────────────────────────
    messageBody: '',
    loadingMessages: false,
    loadingMoreMessages: false,
    hasMoreMessages: false,
    editingMessageUuid: null,
    replyingTo: null,
    quickEmojis: ['👍', '❤️', '😂', '😮', '😢', '🎉'],
    pinnedMessages: [],

    // ── Server-rendered HTML helpers ─────────────────────────
    _initMessagesDom(container) {
      // Initialize Alpine on dynamically injected HTML
      if (typeof Alpine !== 'undefined') Alpine.initTree(container);
      // Attach hover card listeners on @mention badges
      this._initMentionCards(container);
    },

    _initMentionCards(container) {
      // Mention badges have inline onmouseenter/onmouseleave handlers
      // rendered by the server, so no JS init needed. This method exists
      // as a hook point if additional init is ever required.
    },

    async loadMessages(conversationId) {
      this.loadingMessages = true;
      const container = document.getElementById('messages-container');
      if (container) container.innerHTML = ''; // safe: cleared to empty

      try {
        const resp = await fetch(
          `/chat/${conversationId}/messages`,
          { credentials: 'same-origin' }
        );
        // Discard stale response if user already switched conversation
        if (this.activeConversation?.uuid !== conversationId) return;
        if (resp.ok) {
          const html = await resp.text();
          if (container) {
            container.innerHTML = html; // server-rendered trusted HTML
            this._initMessagesDom(container);
            this._readPaginationState();
            // Scroll immediately after HTML injection, before images load
            this.scrollToBottom();
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
      // Race protection: if the user switches conversations while the
      // page-up fetch is in flight, prepending older messages from the
      // previous conversation into the new list would corrupt the view.
      const targetUuid = this.activeConversation.uuid;

      try {
        const resp = await fetch(
          `/chat/${targetUuid}/messages?before=${firstUuid}`,
          { credentials: 'same-origin' }
        );
        if (this.activeConversation?.uuid !== targetUuid) {
          this.loadingMoreMessages = false;
          return;
        }
        if (resp.ok) {
          const html = await resp.text();
          if (this.activeConversation?.uuid !== targetUuid) {
            this.loadingMoreMessages = false;
            return;
          }
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

    _isNearBottom(threshold = 150) {
      const container = this.$refs.messagesContainer;
      if (!container) return true;
      return container.scrollHeight - container.scrollTop - container.clientHeight < threshold;
    },

    scrollToBottom(waitForImages = false) {
      const container = this.$refs.messagesContainer;
      if (!container) return;
      container.scrollTop = container.scrollHeight;

      if (waitForImages) {
        const images = container.querySelectorAll('img:not([complete])');
        images.forEach(img => {
          if (!img.complete) {
            img.addEventListener('load', () => {
              if (this._isNearBottom()) {
                container.scrollTop = container.scrollHeight;
              }
            }, { once: true });
          }
        });
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

      const replyToUuid = this.replyingTo?.uuid || null;
      const replyInfo = this.replyingTo ? { ...this.replyingTo } : null;

      this.messageBody = '';
      this.pendingFiles = [];
      this._lastTypingSent = 0;
      this._clearDraft();
      this.cancelReply();

      // ── Optimistic UI: inject temporary message immediately ──
      const tempId = '_optimistic_' + Date.now();
      const hasFiles = files.length > 0;
      const isBotConv = this.isBotConversation(this.activeConversation);
      this._injectOptimisticMessage(tempId, body, replyInfo, hasFiles ? files : null);
      if (isBotConv) this.botTyping = true;
      this.$nextTick(() => this.scrollToBottom());

      // Revoke object URLs after optimistic bubble is injected
      for (const f of files) {
        if (f._preview) URL.revokeObjectURL(f._preview);
      }

      try {
        let resp;
        if (hasFiles) {
          const formData = new FormData();
          formData.append('body', body);
          if (replyToUuid) formData.append('reply_to_uuid', replyToUuid);
          for (const f of files) {
            formData.append('files', f);
          }
          resp = await fetch(
            `/api/v1/chat/conversations/${this.activeConversation.uuid}/messages`,
            {
              method: 'POST',
              headers: { 'X-CSRFToken': getCSRFToken() },
              credentials: 'same-origin',
              body: formData,
            }
          );
        } else {
          const payload = { body };
          if (replyToUuid) payload.reply_to_uuid = replyToUuid;
          resp = await fetch(
            `/api/v1/chat/conversations/${this.activeConversation.uuid}/messages`,
            {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCSRFToken(),
              },
              credentials: 'same-origin',
              body: JSON.stringify(payload),
            }
          );
        }

        if (resp.ok) {
          const msg = await resp.json();
          this._updateConversationLastMessage(this.activeConversation.uuid, msg);
          // Re-fetch messages — replaces optimistic bubble with real server-rendered one
          await this._refreshCurrentMessages();
          // If bot already replied during the round-trip, hide typing immediately
          if (isBotConv) {
            const lastGroup = document.getElementById('messages-container')?.querySelector('.msg-group:last-child');
            if (lastGroup && lastGroup.classList.contains('msg-group-start')) {
              this.botTyping = false;
            }
          }
          this.$nextTick(() => this.scrollToBottom());
        } else {
          // Remove optimistic message and restore input on error
          this._removeOptimisticMessage(tempId);
          this.messageBody = body;
          this.pendingFiles = files;
          this.botTyping = false;
        }
      } catch (e) {
        console.error('Failed to send message', e);
        this._removeOptimisticMessage(tempId);
        this.messageBody = body;
        this.pendingFiles = files;
        this.botTyping = false;
      }
    },

    _getCurrentUser() {
      if (!this.activeConversation?.members) return null;
      return this.activeConversation.members.find(m => m.user.id === this.currentUserId)?.user;
    },

    _escapeHtml(text) {
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    },

    _injectOptimisticMessage(tempId, body, replyInfo, files) {
      const container = document.getElementById('messages-container');
      if (!container) return;

      const user = this._getCurrentUser();
      const avatarHtml = user
        ? window.userAvatarHtml(user.id, user.username, 'w-8 h-8 text-xs', { presence: false })
        : '';

      // Build body HTML with basic line breaks
      const bodyHtml = body ? this._escapeHtml(body).replace(/\n/g, '<br>') : '';

      // Build reply context HTML
      let replyHtml = '';
      if (replyInfo) {
        replyHtml = `
          <div class="flex gap-2 mb-1.5 rounded-lg px-2 py-1 bg-info/15">
            <div class="w-0.5 flex-shrink-0 rounded-full bg-info"></div>
            <div class="min-w-0 flex-1">
              <span class="text-xs font-semibold text-info">${this._escapeHtml(replyInfo.author)}</span>
              <p class="text-xs text-base-content/70 truncate">${this._escapeHtml(replyInfo.body || '')}</p>
            </div>
          </div>`;
      }

      // Build file previews
      let filesHtml = '';
      if (files && files.length > 0) {
        const items = files.map(f => {
          const name = this._escapeHtml(f.name);
          if (f.type && f.type.startsWith('image/') && f._preview) {
            return `<img src="${f._preview}" alt="${name}" class="max-h-64 max-w-full rounded-lg object-contain cursor-pointer hover:opacity-90 transition-opacity opacity-60" />`;
          }
          if (f.type && f.type.startsWith('video/') && f._preview) {
            return `<div class="relative max-h-64 max-w-full rounded-lg overflow-hidden opacity-60">
              <video src="${f._preview}" class="max-h-64 max-w-full rounded-lg object-contain" preload="metadata"></video>
              <div class="absolute inset-0 flex items-center justify-center bg-black/20">
                <div class="w-12 h-12 rounded-full bg-base-100/80 flex items-center justify-center">
                  <i data-lucide="play" class="w-6 h-6"></i>
                </div>
              </div>
            </div>`;
          }
          const size = f.size ? this.formatFileSize(f.size) : '';
          return `<div class="flex items-center gap-0.5 min-w-0">
            <div class="flex items-center gap-2 p-2 rounded-lg bg-info/15 min-w-0 flex-1">
              <i data-lucide="file" class="w-4 h-4 flex-shrink-0"></i>
              <span class="truncate text-xs font-medium">${name}</span>
              ${size ? `<span class="text-[0.65rem] opacity-60 flex-shrink-0">${size}</span>` : ''}
            </div>
          </div>`;
        }).join('');
        const separator = bodyHtml ? '<div class="border-t border-info/30 my-1.5"></div>' : '';
        const mtClass = bodyHtml ? '' : ' mt-1.5';
        filesHtml = `${separator}<div class="flex flex-col gap-1.5 mb-1.5${mtClass}">${items}</div>`;
      }

      const html = `
        <div class="msg-group msg-group-end flex gap-2 mb-3 flex-row-reverse" id="${tempId}">
          <div class="flex-shrink-0 w-8 mt-auto">${avatarHtml}</div>
          <div class="flex flex-col items-end gap-0.5 min-w-0 max-w-[75%]">
            <div class="relative max-w-full">
              <div class="msg-bubble rounded-2xl px-3 py-1.5 text-sm bg-info/15 text-base-content opacity-70">
                ${replyHtml}
                ${bodyHtml ? `<div class="msg-body prose prose-sm max-w-none break-words">${bodyHtml}</div>` : ''}
                ${filesHtml}
              </div>
            </div>
            <div class="flex items-center gap-1 px-1">
              <span class="loading loading-dots loading-xs text-base-content/40"></span>
            </div>
          </div>
        </div>`;

      container.insertAdjacentHTML('beforeend', html);
    },

    _removeOptimisticMessage(tempId) {
      const el = document.getElementById(tempId);
      if (el) el.remove();
    },

    async _refreshCurrentMessages() {
      // Reload server-rendered messages for the active conversation.
      // Race protection: if the user switches conversations while the
      // fetch is in flight, the response we get back is for the previous
      // conversation; capture the target uuid up front and bail if the
      // active conversation no longer matches when we're about to mutate.
      if (!this.activeConversation) return;
      const container = document.getElementById('messages-container');
      const targetUuid = this.activeConversation.uuid;
      try {
        const resp = await fetch(
          `/chat/${targetUuid}/messages`,
          { credentials: 'same-origin' }
        );
        if (this.activeConversation?.uuid !== targetUuid) return;
        if (resp.ok) {
          const html = await resp.text();
          if (this.activeConversation?.uuid !== targetUuid) return;
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

    // ── Replying ───────────────────────────────────────────
    startReply(uuid, author, body) {
      this.editingMessageUuid = null;
      this.replyingTo = { uuid, author, body };
      this.$nextTick(() => this.getMessageInput()?.focus());
    },

    cancelReply() {
      this.replyingTo = null;
    },

    // ── Editing ────────────────────────────────────────────
    startEdit(msgUuid) {
      const el = document.getElementById(`msg-${msgUuid}`);
      if (!el) return;
      this.editingMessageUuid = msgUuid;
      this.messageBody = el.dataset.body || '';
      this.$nextTick(() => this.getMessageInput()?.focus());
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
              'X-CSRFToken': getCSRFToken(),
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
            headers: { 'X-CSRFToken': getCSRFToken() },
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
              'X-CSRFToken': getCSRFToken(),
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
          headers: { 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
        });
      } catch (e) {
        console.error('Failed to mark as read', e);
      }
    },

    // ── Message pinning ──────────────────────────────────────
    async loadPinnedMessages(conversationId) {
      try {
        const resp = await fetch(`/api/v1/chat/conversations/${conversationId}/pinned-messages`, {
          credentials: 'same-origin',
        });
        if (resp.ok) {
          this.pinnedMessages = await resp.json();
        }
      } catch (e) {
        console.error('Failed to load pinned messages', e);
      }
    },

    async pinMessage(messageId) {
      if (!this.activeConversation) return;
      try {
        const resp = await fetch(`/api/v1/chat/messages/${messageId}/pin`, {
          method: 'POST',
          headers: { 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
        });
        if (resp.ok) {
          await this.loadPinnedMessages(this.activeConversation.uuid);
          await this._refreshCurrentMessages();
        }
      } catch (e) {
        console.error('Failed to pin message', e);
      }
    },

    async unpinMessage(messageId) {
      if (!this.activeConversation) return;
      try {
        const resp = await fetch(`/api/v1/chat/messages/${messageId}/pin`, {
          method: 'DELETE',
          headers: { 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
        });
        if (resp.ok || resp.status === 204) {
          await this.loadPinnedMessages(this.activeConversation.uuid);
          await this._refreshCurrentMessages();
        }
      } catch (e) {
        console.error('Failed to unpin message', e);
      }
    },

    // ── Edit last own message shortcut ───────────────────────
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
};

// Alpine component for the AI question buttons rendered by
// chat/ui/partials/_message_interaction.html. The template instantiates this
// via x-data="messageInteraction()". On click: POST to the answer endpoint,
// then dispatch chat:refresh-messages so the chatApp reloads the partial in
// its answered state (single source of truth = the Django template).
window.messageInteraction = function messageInteraction() {
  return {
    loading: false,
    pendingIndex: null,

    async answer(messageUuid, optionIndex) {
      if (this.loading) return;
      this.loading = true;
      this.pendingIndex = optionIndex;
      try {
        const resp = await fetch(`/api/v1/chat/messages/${messageUuid}/answer`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
          },
          credentials: 'same-origin',
          body: JSON.stringify({ option_index: optionIndex }),
        });

        if (resp.status === 409 || resp.ok) {
          window.dispatchEvent(new CustomEvent('chat:refresh-messages', {
            detail: { reason: 'interaction-answered' },
          }));
          return;
        }

        throw new Error(`HTTP ${resp.status}`);
      } catch (e) {
        console.error('Failed to answer question:', e);
        this.loading = false;
        this.pendingIndex = null;
      }
    },
  };
};
