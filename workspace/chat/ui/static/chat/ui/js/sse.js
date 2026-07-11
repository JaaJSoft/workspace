// SSE event handlers + conversation list helpers triggered by server events.
window.chatSseMixin = function chatSseMixin() {
  return {
    async handleSSEMessage(detail) {
      const isViewing = this.activeConversation && detail.conversation_id === this.activeConversation.uuid;

      if (isViewing) {
        // Hide bot typing indicator if the incoming message is from a bot
        if (this.botTyping && this.isBotMessage(detail.message)) {
          this.botTyping = false;
        }
        // Check if message already exists in the DOM
        if (!document.getElementById(`msg-${detail.message.uuid}`)) {
          const wasAtBottom = this._isNearBottom();
          await this._refreshCurrentMessages();
          if (wasAtBottom) this.scrollToBottom();
          await this.markAsRead(detail.conversation_id);
        }
      }

      this._updateConversationLastMessage(detail.conversation_id, detail.message);
      // Only bump unread if the user is NOT currently viewing this conversation
      if (!isViewing) {
        this._bumpConversationUnread(detail.conversation_id);
      }
      this.refreshConversationItems([detail.conversation_id]);
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

    // Re-fetch the messages container while preserving the user's scroll
    // position. Without this, every SSE-driven refresh (reactions, edits,
    // link previews, pin / unpin, read receipts) would jump the viewport
    // because innerHTML replacement resets scrollTop to 0. We keep the
    // existing simple behaviour: if the user was at the bottom we stay
    // there (so live conversations keep scrolling with new messages),
    // otherwise we restore scrollTop after the DOM swap so the user
    // doesn't lose their reading position when scrolled up.
    async _refreshMessagesPreservingScroll() {
      const container = this.$refs.messagesContainer;
      if (!container) {
        await this._refreshCurrentMessages();
        return;
      }
      const wasAtBottom = this._isNearBottom();
      const prevScrollTop = container.scrollTop;
      const prevScrollHeight = container.scrollHeight;
      await this._refreshCurrentMessages();
      this.$nextTick(() => {
        if (wasAtBottom) {
          this.scrollToBottom();
        } else {
          // Adjust by the height delta so the same content stays in view.
          container.scrollTop = prevScrollTop + (container.scrollHeight - prevScrollHeight);
        }
      });
    },

    handleSSEMessageDeleted(detail) {
      if (this.activeConversation && detail.conversation_id === this.activeConversation.uuid) {
        // Re-fetch to get proper grouping after deletion
        this._refreshMessagesPreservingScroll();
        this.loadPinnedMessages(this.activeConversation.uuid);
      }
    },

    async handleSSEReaction(detail) {
      if (this.activeConversation && detail.conversation_id === this.activeConversation.uuid) {
        await this._refreshMessagesPreservingScroll();
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

    handleSSELinkPreview(detail) {
      if (this.activeConversation && detail.conversation_id === this.activeConversation.uuid) {
        this._refreshMessagesPreservingScroll();
      }
    },

    handleSSEMessagePinned(detail) {
      if (this.activeConversation && detail.conversation_id === this.activeConversation.uuid) {
        this.loadPinnedMessages(this.activeConversation.uuid);
        this._refreshMessagesPreservingScroll();
      }
    },

    handleSSERead(detail) {
      if (this.activeConversation && detail.conversation_id === this.activeConversation.uuid) {
        this._refreshMessagesPreservingScroll();
      }
    },

    handleSSETyping(detail) {
      this.typingUsers = detail;
      clearTimeout(this._typingHideTimer);
      this._typingHideTimer = setTimeout(() => {
        this.typingUsers = {};
      }, 5000);
    },

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
  };
};
