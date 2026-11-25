// SSE event handlers + conversation list helpers triggered by server events.
window.chatSseMixin = function chatSseMixin() {
  return {
    // Conversations the server reported as generating when this connection
    // opened. Kept because a page load races the stream: the announcement
    // can land before a conversation is selected, and selectConversation
    // reads this back once it has one.
    generatingConversations: new Set(),
    // Conversation that received messages while it was open but nobody was
    // looking at the page. Marked read by catchUpUnreadOnReturn.
    unreadWhileAway: null,
    _sseRefreshTimer: null,
    _sseRefreshPins: false,
    _sseRefreshConv: null,

    // An open tab is not someone reading: marking a message read clears its
    // notification, and the push task drops read notifications, so a tab left
    // open on an idle PC would silence the phone.
    _pageIsWatched() {
      return window.pageAttention.isAttended();
    },

    async catchUpUnreadOnReturn() {
      const convId = this.unreadWhileAway;
      if (!convId || !this._pageIsWatched()) return;
      // Claimed before the await so a second trigger does not post twice.
      this.unreadWhileAway = null;
      if (this.activeConversation?.uuid !== convId) return;
      if (!(await this.markAsRead(convId))) {
        // Kept for the next return to the page to retry.
        this.unreadWhileAway ??= convId;
        return;
      }
      const conv = this.conversations.find(c => c.uuid === convId);
      if (conv) conv.unread_count = 0;
      this.refreshConversationItems([convId], { bump: false });
    },

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
            new CustomEvent('thread-reply-received', {
              detail: { root: route.bumpRoot, uuid: detail.message.uuid },
            }),
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
          if (this.hasNewerMessages) {
            // Away from the present the message lands past the end of what
            // is on screen; the newer pages bring it in when the reader gets
            // there. It is still counted read or unread below.
            this._missedLive = true;
          } else {
            const wasAtBottom = this._isNearBottom();
            await this._refreshCurrentMessages();
            this._animateMessageEntry(detail.message.uuid);
            if (wasAtBottom) this.scrollToBottom();
          }
          if (this._pageIsWatched()) {
            await this.markAsRead(detail.conversation_id);
          } else {
            this.unreadWhileAway = detail.conversation_id;
            const conv = this.conversations.find(c => c.uuid === detail.conversation_id);
            if (conv) conv.unread_count = (conv.unread_count || 0) + 1;
          }
        }
      }

      await this._listConversation(detail.conversation_id);
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
            indicator.textContent = gettext('(edited)');
            el.appendChild(indicator);
          }
          el.dataset.body = detail.body;
        });
      }
    },

    // A refresh for an SSE event (reactions, edits, link previews, pins,
    // read receipts) that keeps a reader at the end there, so a live
    // conversation keeps scrolling with new messages. A reader further up
    // is put back on their message by _refreshCurrentMessages itself.
    async _refreshMessagesPreservingScroll() {
      const wasAtBottom = this._isNearBottom();
      await this._refreshCurrentMessages();
      if (wasAtBottom) this.$nextTick(() => this.scrollToBottom());
    },

    _isOpenConversation(detail) {
      return !!this.activeConversation && detail.conversation_id === this.activeConversation.uuid;
    },

    // One SSE poll can carry a burst of reactions, pins, deletions and reads:
    // they share one refetch instead of one each.
    _scheduleSseRefresh({ pins = false } = {}) {
      this._sseRefreshConv = this.activeConversation.uuid;
      this._sseRefreshPins ||= pins;
      if (this._sseRefreshTimer) return;
      this._sseRefreshTimer = setTimeout(() => {
        const refreshPins = this._sseRefreshPins;
        this._sseRefreshTimer = null;
        this._sseRefreshPins = false;
        // A conversation opened since then was loaded fresh.
        if (this.activeConversation?.uuid !== this._sseRefreshConv) return;
        if (refreshPins) this.loadPinnedMessages(this._sseRefreshConv);
        this._refreshMessagesPreservingScroll();
      }, 300);
    },

    handleSSEMessageDeleted(detail) {
      if (this._isOpenConversation(detail)) {
        // Re-fetch to get proper grouping after deletion
        this._scheduleSseRefresh({ pins: true });
      }
    },

    handleSSEReaction(detail) {
      if (this._isOpenConversation(detail)) this._scheduleSseRefresh();
    },

    handleSSEUnread(detail) {
      if (typeof Alpine !== 'undefined' && Alpine.store('chat')) {
        Alpine.store('chat').totalUnread = detail.total;
        Alpine.store('chat').conversationUnreads = detail.conversations;
      }

      for (const conv of this.conversations) {
        const count = detail.conversations[conv.uuid] || 0;
        // The open conversation reads as caught up, unless its read is
        // waiting for the user to come back.
        const isOpen = this.activeConversation && conv.uuid === this.activeConversation.uuid;
        if (isOpen && conv.uuid !== this.unreadWhileAway) {
          conv.unread_count = 0;
        } else {
          conv.unread_count = count;
        }
      }
    },

    handleSSELinkPreview(detail) {
      if (this._isOpenConversation(detail)) this._scheduleSseRefresh();
    },

    handleSSEMessagePinned(detail) {
      if (this._isOpenConversation(detail)) this._scheduleSseRefresh({ pins: true });
    },

    // A read only changes a receipt on the user's own messages that is still
    // waiting on readers (_read_receipt.html marks it). A refresh in flight
    // was answered before this read, so the receipts are judged once it has
    // landed, and the refresh they call for is a request of its own.
    async handleSSERead(detail) {
      if (!this._isOpenConversation(detail)) return;
      if (this._messagesRefreshing) {
        await this._messagesIdle();
        if (!this._isOpenConversation(detail)) return;
      }
      if (!document.querySelector('[data-read-pending]')) return;
      this._scheduleSseRefresh();
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
    // time a connection opens and then every minute while one runs. A reload
    // learns to raise the indicator without waiting for the next tool; arms
    // the same failsafe as a step, because a cancelled generation ends without
    // posting anything - the repeats are what keep that failsafe from firing
    // while the model composes a long reply with no tool to report. The reverse matters
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
    // ending, carrying its id alone. A round runs independent tools
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
      } else if (!this.botSteps.some(s => s.id === detail.call_id)) {
        // A step can arrive twice: a tab joining a stream already open is
        // handed the queued steps, and gets the stream's copy of one queued
        // at that moment.
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

    // A conversation started while the page is open is missing from the list
    // the page was served with, and its row reads its unread dot from there.
    async _listConversation(convId) {
      if (this.conversations.some(c => c.uuid === convId)) return;
      try {
        const resp = await fetch(`/api/v1/chat/conversations/${convId}`, { credentials: 'same-origin' });
        if (!resp.ok) return;
        const conv = await resp.json();
        if (!this.conversations.some(c => c.uuid === conv.uuid)) this.conversations.unshift(conv);
      } catch (e) {
        // The row still shows; only its dot waits for the next page load.
      }
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
