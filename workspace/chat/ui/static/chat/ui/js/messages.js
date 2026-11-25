// Message lifecycle: load, paginate, send (with optimistic UI), edit,
// delete, reply, reactions, mark-as-read, scroll, pin / unpin messages,
// "edit last own message" shortcut.
window.chatMessagesMixin = function chatMessagesMixin() {
  // How long the view keeps following the end of the list once it stops
  // growing, and how far above the end a scroll has to land to mean the
  // reader has left it.
  const FOLLOW_SETTLE_MS = 3000;
  const FOLLOW_SLACK_PX = 8;
  // Rows per page, and the most one request may ask for (the shared
  // paginator's max_limit).
  const PAGE_SIZE = 50;
  const MAX_LIMIT = 1000;

  return {
    // ── State ────────────────────────────────────────────────
    messageBody: '',
    loadingMessages: false,
    // Loads of the message list still waiting on the server, full ones and
    // refreshes alike, and who waits for the last of them to land.
    _messagesRefreshing: 0,
    _messagesIdleWaiters: [],
    loadingMoreMessages: false,
    hasMoreMessages: false,
    // Set while the list shows a stretch of the past cut off from the
    // present (a link to an old message): newer pages load below it, and a
    // refresh never reaches past its end.
    hasNewerMessages: false,
    loadingNewerMessages: false,
    // The rows on screen, as offsets counted from the oldest message of the
    // list: [_windowStart, _windowEnd). The server's offsets never move while
    // the conversation lives (new messages land past the end), so they stay
    // valid from one page to the next.
    _windowStart: 0,
    _windowEnd: 0,
    // Where an older or newer page merges into the items wrapper: read by
    // its x-merge:dynamic binding (message_list.html).
    itemsMerge: 'prepend',
    // A live message arrived while the window was away from the present and
    // was not shown: the newer page that reaches the present may have been
    // cut before it, so one more is asked for then.
    _missedLive: false,
    // Set while a refresh away from the present is out; the message being
    // read is taken when its rows land (_vetoStaleMerge), since the reader
    // may scroll while the request is out.
    _anchorOnMerge: false,
    _refreshAnchor: null,
    editingMessageUuid: null,
    replyingTo: null,
    pinnedMessages: [],
    _unfollowBottom: null,
    msgMenu: {
      open: false, x: 0, y: 0, uuid: null, authorName: '', body: '',
      own: false, deletable: false, pinned: false,
    },

    // ── Surface hooks ────────────────────────────────────────
    // The mixin is spread into more than one component per page (the
    // conversation pane and the thread panel), so every DOM id and endpoint it
    // touches has to be instance-scoped. Defaults are the main conversation
    // surface; the thread panel overrides all of them.
    _messagesContainerId() { return 'messages-container'; },
    _messageListId() { return 'message-list'; },
    // Children the server partial renders inside the list: a state element
    // carrying where the page sits and an items wrapper holding the message
    // groups. Derived from _messageListId() so overriding surfaces get them
    // for free.
    _messageListStateId() { return `${this._messageListId()}-state`; },
    _messageListItemsId() { return `${this._messageListId()}-items`; },
    // Document ids a full load replaces. The thread panel adds the root
    // message, which the server renders outside the paginated list.
    _loadTargets() { return [this._messageListId()]; },
    _messageIdPrefix() { return 'msg'; },
    _messagesBaseUrl() { return `/chat/${this.activeConversation.uuid}/messages`; },
    // query: { limit, offset } for a page, { limit, message } for the page
    // holding a message; without an offset the list opens on its last page.
    _messagesUrl(query = {}) {
      const search = Object.entries(query || {})
        .map(([key, value]) => `${key}=${encodeURIComponent(value)}`)
        .join('&');
      return search ? `${this._messagesBaseUrl()}?${search}` : this._messagesBaseUrl();
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
        return;
      }
      if (this._anchorOnMerge && !this._refreshAnchor) {
        this._refreshAnchor = this._visibleMessage();
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

    // message: open the list on that message instead of the latest page.
    // Resolves to what the server said about it - { targetUuid,
    // targetThreadRoot } - with targetUuid null when the message is unknown
    // or out of reach, which answer alike and fall back to the latest page.
    async loadMessages({ message = null } = {}) {
      const none = { targetUuid: null, targetThreadRoot: null };
      if (this._surfaceGone()) return none;
      this.loadingMessages = true;
      // Drop the previous content so a conversation switch does not show the
      // old conversation under the spinner. Only the items are cleared: the
      // list element itself is a merge target and has to stay in the DOM.
      document.getElementById(this._messageListItemsId())?.replaceChildren();
      this.hasNewerMessages = false;
      this._missedLive = false;

      const query = message ? { limit: PAGE_SIZE, message } : { limit: PAGE_SIZE };
      let shown = none;
      this._messagesLoadStarted();
      try {
        const render = await this.$ajax(this._messagesUrl(query), {
          targets: this._loadTargets(),
          focus: false,
        });
        // A vetoed or superseded response merges nothing; leave the state
        // and scroll position to the request that won.
        if ((render || []).some(Boolean)) {
          shown = this._readPaginationState('full');
          if (message && shown.targetUuid) {
            this._unfollowBottom?.();
          } else {
            // Right after the merge: scrollToBottom keeps up as the
            // attachments take their size.
            this.scrollToBottom();
          }
        }
      } catch (e) {
        console.error('Failed to load messages', e);
      } finally {
        this._messagesLoadSettled();
      }
      this.loadingMessages = false;
      return shown;
    },

    // Reads the state element the last merge left. merged says which part of
    // the window the page was: the whole of it ('full'), or a page added
    // above ('older') or below ('newer') the rows already on screen.
    _readPaginationState(merged = 'full') {
      const state = document.getElementById(this._messageListStateId());
      if (!state) return { targetUuid: null, targetThreadRoot: null };
      const data = state.dataset;
      const offset = parseInt(data.offset, 10) || 0;
      const end = parseInt(data.end, 10) || 0;
      if (merged !== 'newer') {
        this._windowStart = offset;
        this.hasMoreMessages = offset > 0;
      }
      if (merged !== 'older') {
        this._windowEnd = end;
        this.hasNewerMessages = data.hasNewer === 'true';
      }
      return {
        targetUuid: data.targetUuid || null,
        targetThreadRoot: data.targetThreadRoot || null,
      };
    },

    // A day split across two pages gets a separator from each: keep the
    // first. Its messages all come after it.
    _dropRepeatedDays() {
      const items = document.getElementById(this._messageListItemsId());
      let previous = null;
      for (const divider of items?.querySelectorAll?.('[data-day]') || []) {
        if (divider.dataset.day === previous) divider.remove();
        else previous = divider.dataset.day;
      }
    },

    async loadMoreMessages() {
      if (!this.activeConversation || !this.hasMoreMessages || this.loadingMoreMessages) return;
      if (this._surfaceGone() || this._windowStart <= 0) return;

      // The page right above the window, never overlapping it.
      const offset = Math.max(0, this._windowStart - PAGE_SIZE);
      const limit = this._windowStart - offset;

      this.loadingMoreMessages = true;
      const scrollContainer = this.$refs.messagesContainer;
      const prevScrollHeight = scrollContainer.scrollHeight;

      try {
        // Two targets: the state element is replaced, and the older groups
        // are prepended into the items wrapper. Both live inside the list, so
        // a full reload issued meanwhile replaces the list and orphans this
        // request before it can prepend a stale page into fresh content.
        this.itemsMerge = 'prepend';
        const render = await this.$ajax(this._messagesUrl({ limit, offset }), {
          targets: [this._messageListStateId(), this._messageListItemsId()],
          focus: false,
        });
        if ((render || []).some(Boolean)) {
          this._readPaginationState('older');
          this._dropRepeatedDays();
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

    // The page right below the window. It lands at the end of the items,
    // below the viewport, so the scroll position needs no correction.
    async loadNewerMessages() {
      if (!this.activeConversation || !this.hasNewerMessages || this.loadingNewerMessages) return;
      if (this._surfaceGone()) return;

      this.loadingNewerMessages = true;
      let catchUp = false;
      try {
        this.itemsMerge = 'append';
        const render = await this.$ajax(
          this._messagesUrl({ limit: PAGE_SIZE, offset: this._windowEnd }),
          { targets: [this._messageListStateId(), this._messageListItemsId()], focus: false },
        );
        if ((render || []).some(Boolean)) {
          this._readPaginationState('newer');
          this._dropRepeatedDays();
          catchUp = !this.hasNewerMessages && this._missedLive;
        }
      } catch (e) {
        console.error('Failed to load newer messages', e);
      }
      this.loadingNewerMessages = false;
      if (catchUp) {
        this._missedLive = false;
        this.hasNewerMessages = true;
        await this.loadNewerMessages();
      }
    },

    // Back to the latest page, from a stretch of the past.
    jumpToPresent() {
      return this.loadMessages();
    },

    handleScroll() {
      const container = this.$refs.messagesContainer;
      if (!container) return;
      if (container.scrollTop < 50 && this.hasMoreMessages && !this.loadingMoreMessages) {
        this.loadMoreMessages();
      }
      if (this.hasNewerMessages && !this.loadingNewerMessages && this._isNearBottom(50)) {
        this.loadNewerMessages();
      }
    },

    // The message at the top of the viewport and how far below the top of
    // the container it sits, for putting it back after a refresh.
    _visibleMessage() {
      const container = this.$refs.messagesContainer;
      if (!container?.getBoundingClientRect) return null;
      const top = container.getBoundingClientRect().top;
      const prefix = `${this._messageIdPrefix()}-`;
      for (const el of container.querySelectorAll(`[id^="${prefix}"][data-message-uuid]`)) {
        const rect = el.getBoundingClientRect();
        if (rect.bottom > top) {
          return { uuid: el.dataset.messageUuid, offset: rect.top - top };
        }
      }
      return null;
    },

    _restoreVisibleMessage({ uuid, offset }) {
      const container = this.$refs.messagesContainer;
      const el = document.getElementById(`${this._messageIdPrefix()}-${uuid}`);
      if (!container || !el) return;
      const top = container.getBoundingClientRect().top;
      container.scrollTop += el.getBoundingClientRect().top - top - offset;
    },

    // Scroll to a message on this surface and flash it. Returns whether it
    // is on screen to be found. A list just opened on the message jumps
    // ('instant'): gliding from the top of a fresh list takes long enough
    // to read as the wrong place.
    _highlightMessage(uuid, prefix = this._messageIdPrefix(), behavior = 'smooth') {
      const el = document.getElementById(`${prefix}-${uuid}`);
      if (!el) return false;
      el.scrollIntoView({ behavior, block: 'center' });
      el.classList.add('ring-2', 'ring-warning', 'ring-offset-2', 'ring-offset-base-100');
      setTimeout(() => {
        el.classList.remove('ring-2', 'ring-warning', 'ring-offset-2', 'ring-offset-base-100');
      }, 2000);
      return true;
    },

    _isNearBottom(threshold = 150) {
      const container = this.$refs.messagesContainer;
      if (!container) return true;
      return container.scrollHeight - container.scrollTop - container.clientHeight < threshold;
    },

    scrollToBottom() {
      const container = this.$refs.messagesContainer;
      if (!container) return;
      container.scrollTop = container.scrollHeight;
      this._followBottom(container);
    },

    // Keep the newest message in view while the list grows under it: file
    // previews and their pictures take their size after the list is drawn,
    // and each one pushes the end of the conversation further down. Lets go
    // as soon as a scroll moves up away from the end, whatever moved it
    // (wheel, keys, a script), or once the list has kept the same size for
    // FOLLOW_SETTLE_MS. The browser's own scroll anchoring only moves down
    // when content above grows, and a list that shrinks clamps the scroll to
    // its end: neither lets go.
    _followBottom(container) {
      if (this._unfollowBottom) this._unfollowBottom();
      if (typeof ResizeObserver === 'undefined') return;
      let settle = null;
      let observer = null;
      let lastTop = container.scrollTop;
      const onScroll = () => {
        const top = container.scrollTop;
        const fromEnd = container.scrollHeight - top - container.clientHeight;
        if (top < lastTop && fromEnd > FOLLOW_SLACK_PX) unfollow();
        lastTop = top;
      };
      const unfollow = () => {
        if (observer) observer.disconnect();
        clearTimeout(settle);
        container.removeEventListener('scroll', onScroll);
        if (this._unfollowBottom === unfollow) this._unfollowBottom = null;
      };
      observer = new ResizeObserver(() => {
        container.scrollTop = container.scrollHeight;
        lastTop = container.scrollTop;
        clearTimeout(settle);
        settle = setTimeout(unfollow, FOLLOW_SETTLE_MS);
      });
      for (const child of container.children) observer.observe(child);
      container.addEventListener('scroll', onScroll, { passive: true });
      this._unfollowBottom = unfollow;
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
          // Re-render this conversation's sidebar row (and bubble it to the
          // top). The sender is excluded from the SSE broadcast (the
          // receivers' refresh path), so the send path must refresh it itself.
          this.refreshConversationItems([this.activeConversation.uuid]);
          // Re-fetch messages - replaces optimistic bubble with real server-rendered one
          // What was sent belongs at the present. Cleared here, right before
          // the refresh, and not when the send began: a newer page landing
          // meanwhile sets it again, and a failed send keeps the past on
          // screen as it was.
          this.hasNewerMessages = false;
          await this._refreshCurrentMessages();
          // If bot already replied during the round-trip, hide typing immediately
          if (isBotConv) {
            const groups = document.getElementById(this._messagesContainerId())?.querySelectorAll('.msg-group');
            const lastGroup = groups?.[groups.length - 1];
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
          // The sender is excluded from the SSE broadcast, so the send path
          // refreshes its own sidebar row.
          this.refreshConversationItems([convUuid]);
          // What was sent belongs at the present. Cleared here, right before
          // the refresh, and not when the send began: a newer page landing
          // meanwhile sets it again, and a failed send keeps the past on
          // screen as it was.
          this.hasNewerMessages = false;
          await this._refreshCurrentMessages();
          this.$nextTick(() => this.scrollToBottom());
          return true;
        }
        this._removeOptimisticMessage(tempId);
        this.botTyping = false;
        this.clearBotStep?.();
        window.AppAlert.error(gettext('Failed to send the voice message.'));
        return false;
      } catch (e) {
        this._removeOptimisticMessage(tempId);
        this.botTyping = false;
        this.clearBotStep?.();
        window.AppAlert.error(gettext('Failed to send the voice message.'));
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
      const user = this._getCurrentUser();
      if (user) {
        group.setAttribute('author-id', user.id);
        group.setAttribute('author-username', user.username);
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

    async _refreshCurrentMessages() {
      // Reload the surface in place. Unlike loadMessages this never clears
      // first: the replace merge swaps old content for new in one step, so
      // the container cannot flash empty mid-refresh.
      if (!this.activeConversation || this._surfaceGone()) return;
      // The rows on screen stay on screen. A reader following the end gets
      // as many of the latest rows as the window held, so whatever arrived
      // meanwhile shows. Anyone else gets the same rows from the same start,
      // plus room for what arrived when the window reaches the present, and
      // the message being read is put back where it was.
      const away = this.hasNewerMessages;
      const following = !away && this._isNearBottom();
      const rows = Math.max(this._windowEnd - this._windowStart, 1);
      let query;
      if (following) {
        query = { limit: Math.min(Math.max(rows, PAGE_SIZE), MAX_LIMIT) };
      } else {
        const limit = away ? rows : rows + PAGE_SIZE;
        query = { offset: this._windowStart, limit: Math.min(limit, MAX_LIMIT) };
      }
      if (!away) this._missedLive = false;
      this._anchorOnMerge = !following;
      this._refreshAnchor = null;
      this._messagesLoadStarted();
      try {
        const render = await this.$ajax(this._messagesUrl(query), {
          targets: this._loadTargets(),
          focus: false,
        });
        if ((render || []).some(Boolean)) {
          this._readPaginationState('full');
          if (this._refreshAnchor) this._restoreVisibleMessage(this._refreshAnchor);
        }
      } catch (e) {
        console.error('Failed to refresh messages', e);
      } finally {
        this._messagesLoadSettled();
      }
      this._anchorOnMerge = false;
      this._refreshAnchor = null;
    },

    _messagesLoadStarted() {
      this._messagesRefreshing++;
    },

    _messagesLoadSettled() {
      this._messagesRefreshing--;
      if (!this._messagesRefreshing) this._messagesIdleWaiters.splice(0).forEach((resolve) => resolve());
    },

    // Resolves once no load of the message list is in flight.
    _messagesIdle() {
      if (!this._messagesRefreshing) return Promise.resolve();
      return new Promise((resolve) => { this._messagesIdleWaiters.push(resolve); });
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
      this.$nextTick(() => {
        const ta = this.getMessageInput();
        if (!ta) return;
        ta.focus();
        // The formatted editor already puts the caret at the end when it
        // loads the message.
        if (!ta.setSelectionRange) return;
        // Where the caret lands after a programmatic value change is browser
        // dependent, and ArrowUp's own default action pulls it to the start.
        const end = ta.value.length;
        ta.setSelectionRange(end, end);
      });
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
              indicator.textContent = gettext('(edited)');
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
        title: gettext('Delete message'),
        message: gettext('Are you sure you want to delete this message?'),
        okLabel: gettext('Delete'),
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error',
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
            placeholder.textContent = gettext('Message deleted');
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
          this._notifyPeerSurface();
        }
      } catch (e) {
        console.error('Failed to toggle reaction', e);
      }
    },

    // Fan-out of a reaction or pin change to the other surface. The main flow
    // tells the thread panel; the panel overrides this to a no-op because its
    // own _refreshCurrentMessages already asks the main flow to repaint.
    _notifyPeerSurface() {
      window.dispatchEvent(new CustomEvent('chat:refresh-thread'));
    },

    // ── Read status ────────────────────────────────────────
    // Resolves to whether the server recorded the read.
    async markAsRead(conversationId) {
      try {
        const resp = await fetch(`/api/v1/chat/conversations/${conversationId}/read`, {
          method: 'POST',
          headers: { 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
        });
        return resp.ok;
      } catch (e) {
        console.error('Failed to mark as read', e);
        return false;
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
          this._notifyPeerSurface();
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
          this._notifyPeerSurface();
        }
      } catch (e) {
        console.error('Failed to unpin message', e);
      }
    },

    // ── Message context menu ─────────────────────────────────
    // Bound once on the surface's messages container: bubbles are swapped in
    // by alpine-ajax, so a per-bubble listener would have to be re-bound on
    // every load. The bubble's data-* attributes (message_group.html) carry
    // everything the menu needs.
    openMessageContextMenu(event) {
      const bubble = event.target.closest('[data-message-uuid]');
      if (!bubble || !('authorName' in bubble.dataset)) return;
      // The browser's own menu stays reachable where it does something ours
      // cannot: links, media, a text selection, and Shift+right-click.
      if (event.shiftKey || event.target.closest('a, img, video, audio')) return;
      const selection = window.getSelection();
      // containsNode(_, true), not the anchor: a drag started above the bubble
      // anchors outside it yet still selects its text.
      if (selection && !selection.isCollapsed && selection.containsNode(bubble, true)) return;
      event.preventDefault();

      // The main flow and the thread panel each own a menu; right-click does
      // not trigger the other one's click-outside, so close it by hand.
      const previous = window._chatMessageMenuOwner;
      if (previous && previous !== this) previous.msgMenu.open = false;
      window._chatMessageMenuOwner = this;

      const data = bubble.dataset;
      Object.assign(this.msgMenu, {
        uuid: data.messageUuid,
        authorName: data.authorName,
        body: data.body || '',
        own: 'own' in data,
        deletable: 'deletable' in data,
        pinned: 'pinned' in data,
        x: event.clientX,
        y: event.clientY,
        open: true,
      });

      this.$nextTick(() => {
        const menu = document.getElementById(this._messageMenuId());
        if (!menu) return;
        const rect = menu.getBoundingClientRect();
        this.msgMenu.x = Math.max(8, Math.min(event.clientX, window.innerWidth - rect.width - 8));
        this.msgMenu.y = Math.max(8, Math.min(event.clientY, window.innerHeight - rect.height - 8));
      });
    },

    _messageMenuId() { return 'message-context-menu'; },

    // The address that opens the conversation on this message, for anyone
    // who can read it. A thread reply needs nothing more: the server knows
    // its thread and the page opens it.
    messageLink(uuid) {
      return `${window.location.origin}/chat/${this.activeConversation.uuid}?message=${encodeURIComponent(uuid)}`;
    },

    messageMenuAction(action, event) {
      const { uuid, authorName, body } = this.msgMenu;
      this.msgMenu.open = false;
      switch (action) {
        case 'reply':
          // Same preview the hover toolbar passes (Django's truncatechars:100).
          this.startReply(uuid, authorName, body.length > 100 ? `${body.slice(0, 99)}…` : body);
          break;
        case 'react':
          this.openEmojiPicker('reaction', event, uuid);
          break;
        case 'copy':
          navigator.clipboard.writeText(body)
            .then(() => window.AppAlert?.success(gettext('Message copied'), { duration: 2000 }))
            .catch(() => window.AppAlert?.error(gettext('Failed to copy message')));
          break;
        case 'copy-link':
          navigator.clipboard.writeText(this.messageLink(uuid))
            .then(() => window.AppAlert?.success(gettext('Link copied'), { duration: 2000 }))
            .catch(() => window.AppAlert?.error(gettext('Failed to copy link')));
          break;
        case 'pin':
          this.pinMessage(uuid);
          break;
        case 'unpin':
          this.unpinMessage(uuid);
          break;
        case 'edit':
          this.startEdit(uuid);
          break;
        case 'delete':
          this.deleteMessage(uuid);
          break;
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
