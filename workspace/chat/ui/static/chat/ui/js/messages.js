// Message lifecycle: load, paginate, send (with optimistic UI), edit,
// delete, reply, reactions, mark-as-read, scroll, pin / unpin messages,
// "edit last own message" shortcut.

// What to wait before retrying a throttled list when the server did not say.
const CHAT_MESSAGES_RETRY_FALLBACK_SECONDS = 5;
// How long one "could not refresh" complaint stands for. A 5xx under steady
// SSE traffic fails once per incoming message; the user needs to be told, but
// once, not a column of times.
const CHAT_MESSAGES_FAILURE_TOAST_WINDOW_MS = 10000;
// Doubled each time the server says no again, capped at the length of the
// window. Not because a flat delay could not recover - DRF's
// SimpleRateThrottle does not record a rejected request, so the window
// clears either way - but because it spends fewer round-trips getting
// there, and because it is the behaviour that stays correct against a
// throttle that DOES count what it rejects.
const CHAT_MESSAGES_RETRY_MAX_SECONDS = 60;
// alpine-ajax throws this when a response lacks the target and nothing
// cancelled the removal. It is a DOMException: the status is in the message
// and there is nothing else on it - no status field, no headers.
const CHAT_MESSAGES_RENDER_STATUS = /status \[(\d+)\]/;

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
    _refreshInFlight: null,
    _refreshPending: false,
    _refreshRetryTimer: null,
    _lastFailureToastAt: 0,
    _refreshRetryAttempt: 0,

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
    _replyTarget() { return this.replyingTo?.uuid || null; },

    // ── Transport seam ───────────────────────────────────────
    // Four hooks, and everything the mixin sends or fetches goes through
    // them. The member pane addresses the conversation API with the session
    // cookie; the public meeting page (meet.js) spreads this same mixin and
    // re-points all four at /meet/<slug>/..., authenticated by a token
    // header instead. Nothing else in here names an endpoint.
    _messageEndpoint(conversationId) {
      return `/api/v1/chat/conversations/${conversationId}/messages`;
    },
    _messageHeaders({ json = false } = {}) {
      const headers = { 'X-CSRFToken': getCSRFToken() };
      // Never on a multipart send: the boundary is the browser's to write.
      if (json) headers['Content-Type'] = 'application/json';
      return headers;
    },
    // The server-rendered list, fetched as HTML and merged by alpine-ajax.
    _messagesPartialUrl(conversationId, cursor) {
      const base = `/chat/${conversationId}/messages`;
      return cursor ? `${base}?before=${cursor}` : base;
    },
    // Passed as $ajax's `headers` on every load below; alpine-ajax merges
    // them into the fetch it issues. Empty for a member - the cookie is the
    // credential.
    _messagesPartialHeaders() { return {}; },
    _messagesUrl(cursor) {
      return this._messagesPartialUrl(this.activeConversation.uuid, cursor);
    },
    // Whether the person looking is a guest, which the optimistic bubble
    // has to say so the shell skips the presence dot and the profile card
    // the guest cannot reach. The server stamps the same attribute on the
    // groups it renders.
    _viewerIsGuest() { return false; },
    // What a failed send should say. The member pane says nothing: the text
    // and the attachments are back in the composer, which is the message.
    _onSendFailed() {},
    // Whether this surface has a read cursor to move at all. A guest's
    // membership is the meeting, not a conversation row, so it has none.
    _canMarkRead() { return true; },
    // What a merge must be stamped with to belong here. The conversation
    // uuid for a member; the meeting slug for a guest, who never learns the
    // conversation the meeting chat lives in.
    _expectedListKey() { return this.activeConversation?.uuid; },

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
      if (stamped && stamped !== this._expectedListKey()) {
        event.preventDefault();
      }
    },

    // Bound to @ajax:missing on the surface's root element. alpine-ajax's
    // default for a 2xx response that lacks the target id is to REMOVE the
    // live target - a redirect to the login page would silently delete the
    // list. Cancelling keeps it, and turns an error response into "nothing
    // merged" instead of a thrown RenderError.
    _onAjaxMissing(event) {
      if (!event.detail?.target?.closest?.(`#${this._messagesContainerId()}`)) return;
      // Cancelling is what keeps the list, but it is also what stops the
      // RenderError the catch above reads, so the status has to be taken
      // here on the way past. This route is belt rather than the live path:
      // on the SSE-triggered refresh the rejection is what fires, and it
      // carries no headers - so Retry-After is read here when it is
      // available, and the backoff ladder is the policy when it is not.
      const response = event.detail.response;
      if (response && response.ok === false && this._isMessagesPartialUrl(response.url)) {
        this._reportMessagesFailure(response.status, this._retryAfterSeconds(response.headers));
      }
      event.preventDefault();
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
          headers: this._messagesPartialHeaders(),
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
          headers: this._messagesPartialHeaders(),
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
            this._messageEndpoint(this.activeConversation.uuid),
            {
              method: 'POST',
              headers: this._messageHeaders(),
              credentials: 'same-origin',
              body: formData,
            }
          );
        } else {
          const payload = { body };
          if (replyToUuid) payload.reply_to_uuid = replyToUuid;
          if (wsFiles.length > 0) payload.file_uuids = wsFiles.map(f => f.uuid);
          resp = await fetch(
            this._messageEndpoint(this.activeConversation.uuid),
            {
              method: 'POST',
              headers: this._messageHeaders({ json: true }),
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
          this._onSendFailed(resp.status);
        }
      } catch (e) {
        console.error('Failed to send message', e);
        this._removeOptimisticMessage(tempId);
        this.messageBody = body;
        this.pendingFiles = files;
        this.pendingPickedFiles = wsFiles;
        this.botTyping = false;
        this.clearBotStep?.();
        this._onSendFailed(null);
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
          this._messageEndpoint(convUuid),
          {
            method: 'POST',
            headers: this._messageHeaders(),
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

    // The bubble markup is built by the <chat-message-group> shell element
    // (message_shell.js) - the same element message_group.html writes for
    // server-rendered messages, here with its `pending` attribute (reduced
    // opacity, spinner instead of the timestamp). Attributes and properties
    // must all be set BEFORE insertion: the element reads them once, on
    // connect.
    _injectOptimisticMessage(tempId, body, replyInfo, files) {
      // Into the items wrapper, not the container: the next full-list merge
      // is what replaces the optimistic bubble with the real server-rendered
      // message, and it only swaps content inside the list.
      const container = document.getElementById(this._messageListItemsId());
      if (!container) return;

      const group = document.createElement('chat-message-group');
      group.id = tempId;
      group.setAttribute('own', '');
      group.setAttribute('pending', '');
      if (this._viewerIsGuest()) group.setAttribute('viewer-guest', '');
      const user = this._getCurrentUser();
      if (user) {
        // A guest has no user row, so no id and no avatar to fetch - only a
        // name and the badge that says where it comes from.
        if (user.id != null) group.setAttribute('author-id', user.id);
        group.setAttribute('author-username', user.username);
        if (user.is_guest) group.setAttribute('guest', '');
      }
      group.body = body || '';
      group.replyInfo = replyInfo || null;
      group.pendingFiles = files || [];
      // The server-rendered bubble that replaces this one on the next merge
      // does not animate: the entrance was already played here.
      group.classList.add('msg-enter');
      container.appendChild(group);
    },

    // Play the entrance animation on one bubble. Called right after the merge
    // that inserted it: every refresh re-renders the whole list, so the class
    // has to be put on the bubble that is actually new, never on the group.
    _animateMessageEntry(uuid) {
      document.getElementById(`${this._messageIdPrefix()}-${uuid}`)?.classList.add('msg-enter');
    },

    // Restart the entrance animation on the last bubble on screen, so a
    // change of animation preference shows itself without a new message.
    // Reading layout between the removal and the re-add is what makes the
    // browser treat the second add as a fresh animation.
    replayMessageAnimation() {
      const bubbles = document.getElementById(this._messageListItemsId())?.querySelectorAll('.msg-bubble');
      const last = bubbles?.[bubbles.length - 1];
      if (!last) return;
      last.classList.remove('msg-enter');
      void last.offsetWidth;
      last.classList.add('msg-enter');
    },

    _removeOptimisticMessage(tempId) {
      const el = document.getElementById(tempId);
      if (el) el.remove();
    },

    // Reload the surface in place. Unlike loadMessages this never clears
    // first: the replace merge swaps old content for new in one step, so
    // the container cannot flash empty mid-refresh.
    //
    // Coalesced, because the list is re-rendered WHOLE: a refresh issued
    // while another is in flight can only ever produce the answer the one
    // behind it will produce. A thirty-message burst is one repaint, so it
    // is one request out and at most one queued - not thirty fetches racing
    // each other into the same target, which is how a busy conversation
    // used to walk straight into the rate limiter.
    async _refreshCurrentMessages() {
      if (!this.activeConversation || this._surfaceGone()) return;
      if (this._refreshInFlight) {
        this._refreshPending = true;
        return this._refreshInFlight;
      }
      this._refreshInFlight = (async () => {
        try {
          await this._fetchMessagePartial();
          // Drain rather than stop at one: a message arriving during the
          // second request deserves the same repaint the first one got, and
          // each turn of the loop is still a single request. _surfaceGone is
          // re-read every turn: the component can be torn down WHILE the
          // first request is in flight, and a queued request issued after
          // that would merge this panel's thread into whatever panel owns
          // the ids by now - the race _dead exists for.
          while (this._refreshPending && !this._surfaceGone()) {
            this._refreshPending = false;
            await this._fetchMessagePartial();
          }
        } finally {
          this._refreshInFlight = null;
          this._refreshPending = false;
        }
      })();
      return this._refreshInFlight;
    },

    async _fetchMessagePartial() {
      if (this._surfaceGone()) return;
      try {
        const render = await this.$ajax(this._messagesUrl(null), {
          targets: this._loadTargets(),
          headers: this._messagesPartialHeaders(),
          focus: false,
        });
        if ((render || []).some(Boolean)) {
          this._readPaginationState();
          // Content in the list is the only proof of recovery. $ajax also
          // RESOLVES when _onAjaxMissing cancels the removal on a failed
          // response, so resetting on "did not throw" would pin the ladder
          // at its first rung for every refresh that fails that way.
          this._refreshRetryAttempt = 0;
        }
      } catch (e) {
        console.error('Failed to refresh messages', e);
        // The rejection is the only reliable signal: the ajax:error event
        // does reach the root when the swap is driven by a click, but it
        // does not on the SSE-driven refresh - reproduced 3/3 against a
        // spent bucket. The request we issued is by definition ours, so no
        // URL check is needed here.
        const status = this._renderErrorStatus(e);
        if (status !== null) this._reportMessagesFailure(status, null);
      }
    },

    // Bound to @ajax:error on the root that hosts this surface. A non-2xx is
    // a response like any other to alpine-ajax: it parses the body, does not
    // find the list in it, and _onAjaxMissing then cancels the removal -
    // nothing throws, so the catch above never runs and a throttled pane
    // simply stops updating with no sign of it. The detail is flat
    // ({ok, status, url, html, raw, headers}), and the event bubbles up from
    // every $ajax the root issues, so the URL is what tells ours apart.
    onMessagesAjaxError(event) {
      const detail = event?.detail || {};
      if (!this._isMessagesPartialUrl(detail.url)) return;
      this._reportMessagesFailure(detail.status, this._retryAfterSeconds(detail.headers));
    },

    // The one place a failed list swap is turned into something the user can
    // see, reached from three directions because no single one of them fires
    // on every path: the rejection $ajax raises, the missing-target event
    // (the only one carrying headers), and the bubbling ajax:error.
    _reportMessagesFailure(status, retryAfterSeconds) {
      // One complaint per outage: the pending retry IS the "already
      // reported" flag, and it is what makes three routes into one toast.
      if (this._refreshRetryTimer) return;

      if (status === 429) {
        // The server's own figure when it sent one; otherwise back off,
        // because the only path that reaches here without a header is the
        // RenderError, which carries the status and nothing else.
        const backoff = Math.min(
          CHAT_MESSAGES_RETRY_FALLBACK_SECONDS * (2 ** this._refreshRetryAttempt),
          CHAT_MESSAGES_RETRY_MAX_SECONDS,
        );
        const seconds = Number.isFinite(retryAfterSeconds) && retryAfterSeconds > 0
          ? retryAfterSeconds
          : backoff;
        this._refreshRetryAttempt += 1;
        window.AppAlert?.warning('Messages are paused for a moment.');
        this._refreshRetryTimer = setTimeout(() => {
          this._refreshRetryTimer = null;
          this._refreshCurrentMessages();
        }, seconds * 1000);
        return;
      }

      // Nothing to retry against, so there is no pending timer to dedupe on:
      // a window does that job instead.
      const now = Date.now();
      if (now - this._lastFailureToastAt < CHAT_MESSAGES_FAILURE_TOAST_WINDOW_MS) return;
      this._lastFailureToastAt = now;
      window.AppAlert?.error('Could not refresh the messages.');
    },

    _retryAfterSeconds(headers) {
      const advertised = Number.parseInt(headers?.get?.('Retry-After'), 10);
      return Number.isFinite(advertised) ? advertised : null;
    },

    _renderErrorStatus(error) {
      if (!error || error.name !== 'RenderError') return null;
      const found = CHAT_MESSAGES_RENDER_STATUS.exec(error.message || '');
      return found ? Number.parseInt(found[1], 10) : null;
    },

    // Called from each host's own destroy(): a pending retry outliving the
    // surface would repaint a pane nobody is looking at any more. The thread
    // panel and the voice room both tear down while the page lives on, so
    // both call it. chatApp does not: it IS the page, and a root that only
    // ever dies with the document takes its timers with it - a destroy()
    // there would exist for this one line and be wrong the moment a mixin
    // grew one of its own.
    _cancelMessagesRetry() {
      if (this._refreshRetryTimer !== null) {
        clearTimeout(this._refreshRetryTimer);
        this._refreshRetryTimer = null;
      }
    },

    // The event carries an absolute URL and the surface knows a path. The
    // cursor variant only adds a query string, so one comparison covers a
    // full reload and a "load older" page alike - and the leading slash is
    // what stops /chat/xc1/messages from matching /chat/c1/messages.
    _isMessagesPartialUrl(url) {
      // No conversation means no list of ours to have failed - and the chat
      // page binds this on a root that outlives every conversation, so
      // _messagesUrl would be reading uuid off null.
      if (typeof url !== 'string' || !this.activeConversation) return false;
      const path = this._messagesUrl(null);
      const withoutQuery = url.split('?')[0];
      return withoutQuery === path || withoutQuery.endsWith(path);
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
          `${this._messageEndpoint(this.activeConversation.uuid)}/${this.editingMessageUuid}`,
          {
            method: 'PATCH',
            headers: this._messageHeaders({ json: true }),
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
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;

      try {
        const resp = await fetch(
          `${this._messageEndpoint(this.activeConversation.uuid)}/${msgUuid}`,
          {
            method: 'DELETE',
            headers: this._messageHeaders(),
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
      if (!this._canMarkRead()) return;
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
