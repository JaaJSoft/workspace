// SSE event handlers + conversation list helpers triggered by server events.
window.chatSseMixin = function chatSseMixin() {
  return {
    // Conversations the server reported as generating when this connection
    // opened. Kept because a page load races the stream: the announcement
    // can land before a conversation is selected, and selectConversation
    // reads this back once it has one.
    generatingConversations: new Set(),

    async handleSSEMessage(detail) {
      const isViewing = this.activeConversation && detail.conversation_id === this.activeConversation.uuid;
      // Only the bot's own message ends a generation; a human writing while
      // the bot works must not clear the announcement.
      if (this.isBotMessage(detail.message)) {
        this.generatingConversations.delete(detail.conversation_id);
      }

      if (isViewing) {
        // Hide bot typing indicator if the incoming message is from a bot
        if (this.botTyping && this.isBotMessage(detail.message)) {
          this.botTyping = false;
          this.clearBotStep();
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

    // A conversation's title changed server-side (AI regeneration, rename by
    // another member): sync the reactive state, stop the regenerate loader,
    // and re-render the sidebar row in place (no bump to the top - the
    // conversation didn't receive a new message).
    handleSSEConversationUpdated(detail) {
      const conv = this.conversations.find(c => c.uuid === detail.conversation_id);
      if (conv) conv.title = detail.title;
      if (this.activeConversation && this.activeConversation.uuid === detail.conversation_id) {
        this.activeConversation.title = detail.title;
      }
      if (this.titleRegeneratingUuid === detail.conversation_id) {
        clearTimeout(this._titleRegenTimer);
        this.titleRegeneratingUuid = null;
      }
      this.refreshConversationItems([detail.conversation_id], { bump: false });
    },

    // A generation was already under way when this connection opened, so a
    // reload knows to raise the indicator without waiting for the next tool.
    // Arms the same failsafe as a step: a cancelled generation ends without
    // posting anything, so nothing else would ever lower the bubble.
    handleSSEBotGenerating(detail) {
      this.generatingConversations = new Set(detail?.conversation_ids || []);
      if (
        this.activeConversation
        && this.generatingConversations.has(this.activeConversation.uuid)
      ) {
        this.botTyping = true;
        this._armBotStepFailsafe();
      }
    },

    _armBotStepFailsafe() {
      clearTimeout(this._botStepTimer);
      this._botStepTimer = setTimeout(() => {
        this.botSteps = [];
        this.botTyping = false;
      }, 180000);
    },

    // A bot generation progress step (tool execution) arrived. Steps also
    // reach members who didn't send the triggering message (group
    // conversations), so raise the typing indicator for them too. The
    // failsafe timer hides everything again if the generation is cancelled
    // server-side and no completion message ever lands.
    handleSSEBotStep(detail) {
      if (!this.activeConversation || detail.conversation_id !== this.activeConversation.uuid) return;
      this.botTyping = true;
      // Server-rendered summary line (same partial as the final timeline
      // row); steps accumulate so the bubble builds up the timeline live.
      this.botSteps.push({ html: detail.html });
      if (this.botSteps.length > 30) this.botSteps.shift();
      this._armBotStepFailsafe();
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
