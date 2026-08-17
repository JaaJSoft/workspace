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
    pinnedMessages: [],

    // ── Surface hooks ────────────────────────────────────────
    // The mixin is spread into more than one component per page (the
    // conversation pane and the thread panel), so every DOM id and endpoint it
    // touches has to be instance-scoped. Defaults are the main conversation
    // surface; the thread panel overrides all of them.
    _messagesContainerId() { return 'messages-container'; },
    _messageListId() { return 'message-list'; },
    // Children the server partial renders inside the list: a state element
    // carrying the pagination cursor and an items wrapper holding the message
    // groups. Derived from _messageListId() so overriding surfaces get them
    // for free.
    _messageListStateId() { return `${this._messageListId()}-state`; },
    _messageListItemsId() { return `${this._messageListId()}-items`; },
    // Document ids a full load replaces. The thread panel adds the root
    // message, which the server renders outside the paginated list.
    _loadTargets() { return [this._messageListId()]; },
    _messageIdPrefix() { return 'msg'; },
    _messagesUrl(cursor) {
      const base = `/chat/${this.activeConversation.uuid}/messages`;
      return cursor ? `${base}?before=${cursor}` : base;
    },
    _replyTarget() { return this.replyingTo?.uuid || null; },

    // Every rendered copy of a message, across surfaces. With the inline
    // preference on and the thread panel open, one message is on screen twice,
    // so a state change (edit, delete, reaction) has to reach both. Use this
    // for updates; use `_messageIdPrefix()` + getElementById when you mean
    // "the copy on THIS surface", such as a scroll target.
    _messageEls(uuid) {
      return document.querySelectorAll(`[data-message-uuid="${uuid}"]`);
    },

    // Whether this component's surface has been torn down. A component can
    // outlive its DOM for a moment (an awaited chain resuming after the
    // panel was destroyed): issuing a request then would merge content into
    // whatever NEW panel owns the ids by now, so every load below checks
    // this before asking alpine-ajax for anything. The thread panel flips it
    // in destroy(). Responses already in flight need no guard here:
    // alpine-ajax resolved their target elements when the request was
    // issued, and skips a target that has left the DOM.
    _surfaceGone() { return false; },

    // ── Server-rendered swaps (alpine-ajax) ──────────────────
    // All three loads below go through $ajax: the server partial is merged
    // into per-surface targets and Alpine initializes the swapped subtree
    // itself. Overlapping requests need no generation token any more - for a
    // given target element alpine-ajax only merges the most recently issued
    // request, and a full reload replaces the list wholesale, orphaning any
    // pending pagination merge aimed inside it. The one guard that stays on
    // our side is _vetoStaleMerge below.

    // Bound to @ajax:merge on the surface's messages container. A response
    // rendered for another conversation must not land in this one: the pane
    // is NOT torn down on a conversation switch, so alpine-ajax's own
    // bookkeeping (newest request per element wins, disconnected targets are
    // skipped) cannot see the mismatch during the switch itself. The server
    // stamps data-conversation-uuid on every mergeable element; content
    // without a stamp is let through.
    _vetoStaleMerge(event) {
      const stamped = event.detail?.content?.getAttribute?.('data-conversation-uuid');
      if (stamped && stamped !== this.activeConversation?.uuid) {
        event.preventDefault();
      }
    },

    // Bound to @ajax:missing on the surface's root element. alpine-ajax's
    // default for a 2xx response that lacks the target id is to REMOVE the
    // live target - a redirect to the login page would silently delete the
    // list. Cancelling keeps it, and turns an error response into "nothing
    // merged" instead of a thrown RenderError.
    _onAjaxMissing(event) {
      if (event.detail?.target?.closest?.(`#${this._messagesContainerId()}`)) {
        event.preventDefault();
      }
    },

    async loadMessages() {
      if (this._surfaceGone()) return;
      this.loadingMessages = true;
      // Drop the previous content so a conversation switch does not show the
      // old conversation under the spinner. Only the items are cleared: the
      // list element itself is a merge target and has to stay in the DOM.
      document.getElementById(this._messageListItemsId())?.replaceChildren();

      try {
        const render = await this.$ajax(this._messagesUrl(null), {
          targets: this._loadTargets(),
          focus: false,
        });
        // A vetoed or superseded response merges nothing; leave the state
        // and scroll position to the request that won.
        if ((render || []).some(Boolean)) {
          this._readPaginationState();
          // Scroll immediately after the merge, before images load
          this.scrollToBottom();
        }
      } catch (e) {
        console.error('Failed to load messages', e);
      }
      this.loadingMessages = false;
    },

    _readPaginationState() {
      const state = document.getElementById(this._messageListStateId());
      if (!state) return;
      this.hasMoreMessages = state.dataset.hasMore === 'true';
    },

    async loadMoreMessages() {
      if (!this.activeConversation || !this.hasMoreMessages || this.loadingMoreMessages) return;
      if (this._surfaceGone()) return;

      const cursor = document.getElementById(this._messageListStateId())?.dataset.firstUuid;
      if (!cursor) return;

      this.loadingMoreMessages = true;
      const scrollContainer = this.$refs.messagesContainer;
      const prevScrollHeight = scrollContainer.scrollHeight;

      try {
        // Two targets: the state element is replaced (fresh cursor), and the
        // older groups are prepended into the items wrapper - the partial
        // carries x-merge="prepend" on it. Both live inside the list, so a
        // full reload issued meanwhile replaces the list and orphans this
        // request before it can prepend a stale page into fresh content.
        const render = await this.$ajax(this._messagesUrl(cursor), {
          targets: [this._messageListStateId(), this._messageListItemsId()],
          focus: false,
        });
        if ((render || []).some(Boolean)) {
          this._readPaginationState();
          // Maintain scroll position
          this.$nextTick(() => {
            scrollContainer.scrollTop = scrollContainer.scrollHeight - prevScrollHeight;
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
      const wsFiles = [...(this.pendingPickedFiles || [])];
      if ((!body && files.length === 0 && wsFiles.length === 0) || !this.activeConversation) return;

      const replyToUuid = this._replyTarget();
      const replyInfo = this.replyingTo ? { ...this.replyingTo } : null;

      this.messageBody = '';
      this.pendingFiles = [];
      this.pendingPickedFiles = [];
      this._lastTypingSent = 0;
      this._clearDraft();
      this.cancelReply();

      // ── Optimistic UI: inject temporary message immediately ──
      const tempId = '_optimistic_' + Date.now();
      const hasFiles = files.length > 0 || wsFiles.length > 0;
      const isBotConv = this.isBotConversation(this.activeConversation);
      this._injectOptimisticMessage(tempId, body, replyInfo, hasFiles ? files : null);
      if (isBotConv) {
        this.botTyping = true;
        this.clearBotStep?.();
      }
      this.$nextTick(() => this.scrollToBottom());

      // Revoke object URLs after optimistic bubble is injected
      for (const f of files) {
        if (f._preview) URL.revokeObjectURL(f._preview);
      }

      try {
        let resp;
        if (files.length > 0) {
          const formData = new FormData();
          formData.append('body', body);
          if (replyToUuid) formData.append('reply_to_uuid', replyToUuid);
          for (const f of files) {
            formData.append('files', f);
          }
          for (const wf of wsFiles) {
            formData.append('file_uuids', wf.uuid);
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
          if (wsFiles.length > 0) payload.file_uuids = wsFiles.map(f => f.uuid);
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
          // Re-render this conversation's sidebar row (and bubble it to the
          // top). The sender is excluded from the SSE broadcast (the
          // receivers' refresh path), so the send path must refresh it itself.
          this.refreshConversationItems([this.activeConversation.uuid]);
          // Re-fetch messages — replaces optimistic bubble with real server-rendered one
          await this._refreshCurrentMessages();
          // If bot already replied during the round-trip, hide typing immediately
          if (isBotConv) {
            const lastGroup = document.getElementById(this._messagesContainerId())?.querySelector('.msg-group:last-child');
            if (lastGroup && lastGroup.classList.contains('msg-group-start')) {
              this.botTyping = false;
              this.clearBotStep?.();
            }
          }
          this.$nextTick(() => this.scrollToBottom());
        } else {
          // Remove optimistic message and restore input on error
          this._removeOptimisticMessage(tempId);
          this.messageBody = body;
          this.pendingFiles = files;
          this.pendingPickedFiles = wsFiles;
          this.botTyping = false;
          this.clearBotStep?.();
        }
      } catch (e) {
        console.error('Failed to send message', e);
        this._removeOptimisticMessage(tempId);
        this.messageBody = body;
        this.pendingFiles = files;
        this.pendingPickedFiles = wsFiles;
        this.botTyping = false;
        this.clearBotStep?.();
      }
    },

    // A voice recording is sent on its own: the API pairs `duration` with a
    // single uploaded file, and the recorder replaces the composer while
    // active so there is nothing else pending. Returns whether the message
    // reached the server: the recorder keeps the blob when it did not, so the
    // user can retry.
    async sendVoiceMessage(file, duration) {
      if (!this.activeConversation) return false;
      const convUuid = this.activeConversation.uuid;
      const replyToUuid = this._replyTarget();
      const replyInfo = this.replyingTo ? { ...this.replyingTo } : null;
      const isBotConv = this.isBotConversation(this.activeConversation);
      this.cancelReply();

      const tempId = '_optimistic_' + Date.now();
      this._injectOptimisticMessage(tempId, '', replyInfo, [file]);
      if (isBotConv) {
        this.botTyping = true;
        this.clearBotStep?.();
      }
      this.$nextTick(() => this.scrollToBottom());

      const formData = new FormData();
      formData.append('body', '');
      formData.append('files', file);
      formData.append('duration', String(duration));
      if (replyToUuid) formData.append('reply_to_uuid', replyToUuid);

      try {
        const resp = await fetch(
          `/api/v1/chat/conversations/${convUuid}/messages`,
          {
            method: 'POST',
            headers: { 'X-CSRFToken': getCSRFToken() },
            credentials: 'same-origin',
            body: formData,
          }
        );
        if (resp.ok) {
          const msg = await resp.json();
          this._updateConversationLastMessage(convUuid, msg);
          // The sender is excluded from the SSE broadcast, so the send path
          // refreshes its own sidebar row.
          this.refreshConversationItems([convUuid]);
          await this._refreshCurrentMessages();
          this.$nextTick(() => this.scrollToBottom());
          return true;
        }
        this._removeOptimisticMessage(tempId);
        this.botTyping = false;
        this.clearBotStep?.();
        window.AppAlert.error('Failed to send the voice message.');
        return false;
      } catch (e) {
        this._removeOptimisticMessage(tempId);
        this.botTyping = false;
        this.clearBotStep?.();
        window.AppAlert.error('Failed to send the voice message.');
        return false;
      }
    },

    _getCurrentUser() {
      if (!this.activeConversation?.members) return null;
      return this.activeConversation.members.find(m => m.user.id === this.currentUserId)?.user;
    },

    // The bubble markup lives in _optimistic_message.html (<template>
    // elements rendered next to the messages container), not here: it is the
    // pending-state twin of message_group.html and both get restyled
    // together. Fills the template's __TOKEN__ placeholders in a single
    // left-to-right pass, so a token-looking sequence inside a substituted
    // value (user text is escaped, but stays user-controlled) is never
    // itself substituted.
    _optimisticTpl(templateId, values = {}) {
      const tpl = document.getElementById(templateId);
      if (!tpl) return '';
      return tpl.innerHTML.replace(
        /__([A-Z_]+?)__/g,
        (token, name) => (Object.hasOwn(values, name) ? values[name] : token),
      );
    },

    _injectOptimisticMessage(tempId, body, replyInfo, files) {
      // Into the items wrapper, not the container: the next full-list merge
      // is what replaces the optimistic bubble with the server-rendered
      // message, and it only swaps content inside the list.
      const container = document.getElementById(this._messageListItemsId());
      if (!container) return;

      const user = this._getCurrentUser();
      const avatarHtml = user
        ? window.userAvatarTag(user.id, user.username, { size: 'sm' })
        : '';

      // Body HTML with basic line breaks
      const bodyHtml = body ? escapeHtml(body).replace(/\n/g, '<br>') : '';

      const replyHtml = replyInfo
        ? this._optimisticTpl('chat-optimistic-reply', {
            AUTHOR: escapeHtml(replyInfo.author),
            PREVIEW: escapeHtml(replyInfo.body || ''),
          })
        : '';

      let filesHtml = '';
      if (files && files.length > 0) {
        const items = files.map(f => {
          const name = escapeHtml(f.name);
          if (f.type && f.type.startsWith('image/') && f._preview) {
            return this._optimisticTpl('chat-optimistic-image', { SRC: f._preview, NAME: name });
          }
          if (f.type && f.type.startsWith('video/') && f._preview) {
            return this._optimisticTpl('chat-optimistic-video', { SRC: f._preview });
          }
          const sizeChip = f.size
            ? this._optimisticTpl('chat-optimistic-file-size', { SIZE: formatFileSize(f.size) })
            : '';
          return this._optimisticTpl('chat-optimistic-file', { NAME: name, SIZE_CHIP: sizeChip });
        }).join('');
        filesHtml = this._optimisticTpl('chat-optimistic-files', {
          SEPARATOR: bodyHtml ? this._optimisticTpl('chat-optimistic-separator') : '',
          MT: bodyHtml ? '' : ' mt-1.5',
          ITEMS: items,
        });
      }

      const html = this._optimisticTpl('chat-optimistic-message', {
        ID: tempId,
        AVATAR: avatarHtml,
        REPLY: replyHtml,
        BODY: bodyHtml
          ? this._optimisticTpl('chat-optimistic-body', { BODY_HTML: bodyHtml })
          : '',
        FILES: filesHtml,
      });
      if (html) container.insertAdjacentHTML('beforeend', html);
    },

    _removeOptimisticMessage(tempId) {
      const el = document.getElementById(tempId);
      if (el) el.remove();
    },

    async _refreshCurrentMessages() {
      // Reload the surface in place. Unlike loadMessages this never clears
      // first: the replace merge swaps old content for new in one step, so
      // the container cannot flash empty mid-refresh.
      if (!this.activeConversation || this._surfaceGone()) return;
      try {
        const render = await this.$ajax(this._messagesUrl(null), {
          targets: this._loadTargets(),
          focus: false,
        });
        if ((render || []).some(Boolean)) this._readPaginationState();
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
      const el = this._messageEls(msgUuid)[0];
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
          // Update every rendered copy, not just the first: the message can be
          // on screen in both the main flow and the thread panel.
          this._messageEls(updated.uuid).forEach((el) => {
            const bodyEl = el.querySelector('.msg-body');
            if (bodyEl) bodyEl.innerHTML = updated.body_html;
            // data-body too, not just the rendered HTML: startEdit reads it
            // back to prefill the composer, so leaving it stale makes the next
            // edit start from the pre-edit text.
            el.dataset.body = updated.body;
            // Add edited indicator if not already present
            if (!el.querySelector('.edited-indicator')) {
              const indicator = document.createElement('span');
              indicator.className = 'text-[0.65rem] opacity-50 italic ml-1 edited-indicator';
              indicator.textContent = '(edited)';
              el.appendChild(indicator);
            }
          });
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
          // Replace every rendered copy with a "deleted" placeholder, keeping
          // each one's own id so its surface's scroll anchors still resolve.
          this._messageEls(msgUuid).forEach((el) => {
            // Replace the entire row (parent .group/msg div) with a simple deleted indicator
            const row = el.closest('.group\\/msg') || el.parentElement;
            const placeholder = document.createElement('div');
            placeholder.className = 'msg-bubble rounded-2xl px-3 py-1.5 text-sm italic bg-base-200 text-base-content/40';
            placeholder.id = el.id;
            placeholder.dataset.messageUuid = msgUuid;
            placeholder.textContent = 'Message deleted';
            row.replaceWith(placeholder);
          });
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
          // Then whatever other surface shows a copy of this message: the
          // refresh above only repaints the surface the click landed on.
          this._notifyReactionPeers();
        }
      } catch (e) {
        console.error('Failed to toggle reaction', e);
      }
    },

    // Reaction fan-out to the other surface. The main flow tells the thread
    // panel; the panel overrides this to a no-op because its own
    // _refreshCurrentMessages already asks the main flow to repaint.
    _notifyReactionPeers() {
      window.dispatchEvent(new CustomEvent('chat:refresh-thread'));
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
      const container = document.getElementById(this._messagesContainerId());
      if (!container) return;

      const bubbles = container.querySelectorAll('.msg-bubble[data-body]');
      // Walk backwards to find the last one from current user
      for (let i = bubbles.length - 1; i >= 0; i--) {
        const bubble = bubbles[i];
        // msg-group-end marks own messages (chat-end / right-aligned)
        if (bubble.closest('.msg-group-end')) {
          // The surface's own prefix: a hard-coded 'msg-' would mangle the
          // thread panel's tmsg- ids into t<uuid> and the edit would no-op.
          const msgId = bubble.id?.replace(`${this._messageIdPrefix()}-`, '');
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
// via x-data="messageInteraction()". On click: inject an optimistic message
// bubble for the chosen option, POST to the answer endpoint, then dispatch
// chat:refresh-messages so the chatApp reloads the partial in its answered
// state.
window.messageInteraction = function messageInteraction() {
  return {
    loading: false,
    pendingIndex: null,

    async answer(messageUuid, optionIndex, optionLabel) {
      if (this.loading) return;
      this.loading = true;
      this.pendingIndex = optionIndex;

      const tempId = '_optimistic_answer_' + Date.now();
      window.dispatchEvent(new CustomEvent('chat:answer-optimistic', {
        detail: { tempId, body: optionLabel },
      }));

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
        window.dispatchEvent(new CustomEvent('chat:answer-optimistic-rollback', {
          detail: { tempId },
        }));
        this.loading = false;
        this.pendingIndex = null;
      }
    },
  };
};
