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

      // Where this message belongs. A thread reply skips the main flow unless
      // the user asked to see replies inline, and reaches the panel through a
      // window event rather than by being appended here: the panel owns its
      // own container and reloads itself.
      const route = window.chatThreadRouteTargets(detail, {
        openThreadRoot: this.openThreadRoot,
        showInline: !!this.chatPrefs?.showThreadRepliesInline,
      });
      if (route.bumpRoot) {
        this._bumpRenderedReplyCount(route.bumpRoot);
        if (route.panel) {
          // The user is looking at this thread, so the reply must not survive
          // as unread: the server already counted it on both the participant
          // row and the conversation badge, and only the read endpoint moves
          // those back. Fire-and-forget, same as openThread's call.
          this.markThreadRead(route.bumpRoot);
          window.dispatchEvent(
            new CustomEvent('thread-reply-received', { detail: { root: route.bumpRoot } }),
          );
        } else {
          this.bumpThreadUnread(route.bumpRoot);
        }
      }

      if (isViewing && route.mainFlow) {
        // Hide bot typing indicator if the incoming message is from a bot
        if (this.botTyping && this.isBotMessage(detail.message)) {
          this.botTyping = false;
          this.clearBotStep();
        }
        if (!document.getElementById(`${this._messageIdPrefix()}-${detail.message.uuid}`)) {
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
        // Every copy: the message can be rendered in the main flow and in the
        // thread panel at the same time.
        this._messageEls(detail.message_id).forEach((el) => {
          const bodyEl = el.querySelector('.msg-body');
          if (bodyEl) bodyEl.innerHTML = detail.body_html;
          if (!el.querySelector('.edited-indicator')) {
            const indicator = document.createElement('span');
            indicator.className = 'text-[0.65rem] opacity-50 italic ml-1 edited-indicator';
            indicator.textContent = '(edited)';
            el.appendChild(indicator);
          }
          el.dataset.body = detail.body;
        });
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

    // Snapshot of the conversations with a generation under way, sent every
    // time a connection opens. A reload learns to raise the indicator without
    // waiting for the next tool; arms the same failsafe as a step, because a
    // cancelled generation ends without posting anything. The reverse matters
    // just as much: a reconnect (mobile resume) opens a fresh EventSource
    // that replays no message events, so a bubble raised before the stream
    // dropped is lowered here when its conversation left the snapshot - the
    // reply itself lands through the reconnect catch-up refresh.
    handleSSEBotGenerating(detail) {
      this.generatingConversations = new Set(detail?.conversation_ids || []);
      if (!this.activeConversation) return;
      if (this.generatingConversations.has(this.activeConversation.uuid)) {
        this.botTyping = true;
        this._armBotStepFailsafe();
      } else if (this.botTyping) {
        this.botTyping = false;
        this.clearBotStep();
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
    //
    // Two kinds arrive: a call starting, carrying its server-rendered
    // summary line (same partial as the final timeline row), and a call
    // ending, carrying its id alone. A round runs its read-only tools
    // together, so the one that ends is not necessarily the last one shown.
    handleSSEBotStep(detail) {
      if (!this.activeConversation || detail.conversation_id !== this.activeConversation.uuid) return;
      this.botTyping = true;
      if (detail.done) {
        // Its opening step may have fallen out of the capped mailbox, or
        // been queued before this connection opened: there is then no row
        // to end, and nothing to do.
        const step = this.botSteps.find(s => s.id === detail.call_id);
        if (step) step.running = false;
      } else {
        this.botSteps.push({ id: detail.call_id, html: detail.html, running: true });
        if (this.botSteps.length > 30) this.botSteps.shift();
      }
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
