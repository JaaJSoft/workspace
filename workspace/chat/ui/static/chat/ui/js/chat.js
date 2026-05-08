function chatApp(currentUserId) {
  // Compute the initial sidebar state synchronously so the very first
  // Alpine binding pass paints the correct width class. Without this,
  // a mobile viewport with a desktop-era localStorage value of `false`
  // would render w-80 first, then snap to w-16 once init() awaits
  // resolved — a visible "expanded → collapsed" flicker on load.
  const isMobileViewport = window.matchMedia('(max-width: 1023px)').matches;
  return {
    // ── Identity + persistent UI state ──────────────────────
    currentUserId: currentUserId,
    collapsed: isMobileViewport
      || JSON.parse(localStorage.getItem('chatSidebarCollapsed') || 'false'),

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

      // ?action=new — open new conversation dialog from command palette
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

      // Auto-select conversation from URL (e.g. /chat/<uuid>)
      if (!dmParam) {
        const initialEl = document.getElementById('initial-conversation');
        if (initialEl) {
          try {
            const uuid = JSON.parse(initialEl.textContent);
            if (uuid) {
              // Replace current history entry so back goes to /chat
              history.replaceState({ conversationUuid: uuid }, '', `/chat/${uuid}`);
              await this.selectConversationById(uuid, false);
            }
          } catch (e) {
            console.error('Failed to parse initial conversation', e);
          }
        }
      }

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
