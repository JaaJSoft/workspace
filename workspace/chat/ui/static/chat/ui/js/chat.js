function chatApp(currentUserId) {
  return {
    // ── Identity + persistent UI state ──────────────────────
    currentUserId: currentUserId,
    // Seed `collapsed` synchronously from the viewport so Alpine's first
    // binding pass paints the correct width class. Without the mobile
    // check here, a mobile load after a desktop session left the sidebar
    // expanded (localStorage = "false") would render w-80 first and snap
    // to w-16 once init() ran — a visible "expanded → collapsed" flicker.
    collapsed: window.matchMedia('(max-width: 1023px)').matches
      || JSON.parse(localStorage.getItem('chatSidebarCollapsed') || 'false'),
    // Gate the sidebar's width transition. Defer-loaded Alpine binds the
    // `:class` on the aside *after* the first paint, so any width applied
    // here would animate from the unstyled state. Keep `transition-all`
    // off until $nextTick has flushed the bind, then enable it for
    // subsequent toggleCollapse() calls.
    sidebarMounted: false,

    // Reactive copy of chat preferences. Seeded synchronously from the
    // global cache so the first Alpine paint already has the right
    // density flags, then re-hydrated in init() once _chatPrefsReady
    // resolves (in case the cache was still falling back to defaults).
    chatPrefs: { ...window._chatPrefsCache },

    // ── Compose chatApp from domain mixins ──────────────────
    // Each mixin returns an object literal with its own state and
    // methods, and we spread them so they all share `this` at runtime.
    // Order matters when two mixins define the same key — later spreads
    // override earlier ones. Today the only intentional override is
    // chatInputMixin.formatFileSize taking precedence over no longer
    // existing duplicates from older revisions.
    ...chatConversationsMixin(),
    ...chatMessagesMixin(),
    ...chatSseMixin(),
    ...chatMembersMixin(),
    ...chatPanelsMixin(),
    ...chatBotMixin(),
    ...chatInputMixin(),

    // ── Init: orchestrates first paint and global listeners ─
    async init() {
      // Re-enable the sidebar width transition after Alpine has finished
      // its initial bind, so toggleCollapse() animates smoothly without
      // animating the very first paint.
      this.$nextTick(() => { this.sidebarMounted = true; });

      // Hydrate chat preferences from the server once the initial fetch
      // resolved, and keep listening for cross-component updates fired
      // by the preferences popover/dialog.
      window._chatPrefsReady.then(() => {
        this.chatPrefs = { ...window._chatPrefsCache };
      });
      window.addEventListener('chat:preferences-changed', (e) => {
        this.chatPrefs = { ...e.detail };
      });

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

      // Auto-collapse when the viewport shrinks into the mobile range.
      // The initial mobile check happens synchronously above (in the
      // factory) to avoid a first-paint flicker; this listener only
      // handles later resize transitions.
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

      // Save draft on page unload
      window.addEventListener('beforeunload', () => this._saveDraft());

      // Catch up on missed events when SSE reconnects (mobile resume)
      window.addEventListener('sse:reconnect', () => {
        this.loadConversations();
        if (this.activeConversation) {
          this._refreshCurrentMessages();
        }
      });

      // Save-to-files from attachment viewer modal
      window.addEventListener('chat-save-attachment-to-files', (e) => {
        this.saveAttachmentToFiles(e.detail.uuid);
      });

      // Refresh messages after an interactive question is answered, so the
      // server-rendered partial re-paints in its answered state.
      window.addEventListener('chat:refresh-messages', () => {
        if (this.activeConversation?.uuid && typeof this.loadMessages === 'function') {
          this.loadMessages(this.activeConversation.uuid);
        }
      });

      // ?action=new - open new conversation dialog from command palette
      const params = new URLSearchParams(window.location.search);
      const action = params.get('action');
      if (action === 'new') {
        this.$nextTick(() => this.showNewConversationDialog());
        const url = new URL(window.location);
        url.searchParams.delete('action');
        history.replaceState(null, '', url);
      }

      // Auto-open DM from query param (e.g. /chat?dm=42)
      const dmParam = params.get('dm');
      if (dmParam) {
        await this._openDmByUserId(parseInt(dmParam, 10));
      }

      // Auto-select conversation from URL (e.g. /chat/<uuid>). The UUID was
      // already read synchronously in chatConversationsMixin (and stashed in
      // `pendingInitialConvUuid`) so the first paint can hide the mobile
      // drawer; here we just trigger the actual fetch + selection.
      if (!dmParam && this.pendingInitialConvUuid) {
        const uuid = this.pendingInitialConvUuid;
        // Replace current history entry so back goes to /chat
        history.replaceState({ conversationUuid: uuid }, '', `/chat/${uuid}`);
        await this.selectConversationById(uuid, false);
      }
      this.pendingInitialConvUuid = null;

      // Fetch available AI bots
      this.fetchBots();

      // Emoji picker event listener
      this.$nextTick(() => {
        const picker = this.$refs.emojiPicker;
        if (picker) {
          picker.addEventListener('emoji-click', (e) => {
            const unicode = e.detail.unicode;
            if (this.emojiPickerMode === 'input') {
              this.insertEmoji(unicode);
            } else if (this.emojiPickerMode === 'reaction' && this.emojiPickerTargetMsg) {
              this.toggleReaction(this.emojiPickerTargetMsg, unicode);
            }
            this.closeEmojiPicker();
          });
        }
      });
    },

    // ── Sidebar collapse ───────────────────────────────────
    toggleCollapse() {
      this.collapsed = !this.collapsed;
      localStorage.setItem('chatSidebarCollapsed', JSON.stringify(this.collapsed));
    },

    isMobile() {
      return window.matchMedia('(max-width: 1023px)').matches;
    },

    isSmallScreen() {
      return window.matchMedia('(max-width: 639px)').matches;
    },

    getMessageInput() {
      return this.isSmallScreen()
        ? this.$refs.messageInputMobile
        : this.$refs.messageInput;
    },

    // ── Generic helpers (shared across mixins) ──────────────
    formatDate(iso) {
      if (!iso) return '';
      const d = new Date(iso);
      return d.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
    },

    formatDateTime(iso) {
      if (!iso) return '';
      const d = new Date(iso);
      return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
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
  };
}
