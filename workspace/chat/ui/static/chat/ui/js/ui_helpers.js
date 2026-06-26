// Shared UI helpers used by both the chat page (chatApp) and the voice room
// (chatRoomApp): viewport checks, the active composer ref, autoresize, and
// display/date formatting. Kept in a mixin so both factories expose them.
window.chatUiHelpersMixin = function chatUiHelpersMixin() {
  return {
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
};
