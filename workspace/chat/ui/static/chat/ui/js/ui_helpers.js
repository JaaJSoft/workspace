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
      const tz = window.getUserTimeZone ? window.getUserTimeZone() : undefined;
      const d = new Date(iso);
      return d.toLocaleDateString(undefined, { timeZone: tz, year: 'numeric', month: 'long', day: 'numeric' });
    },

    formatDateTime(iso) {
      if (!iso) return '';
      const tz = window.getUserTimeZone ? window.getUserTimeZone() : undefined;
      const d = new Date(iso);
      return d.toLocaleDateString(undefined, { timeZone: tz, year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    },

    memberDisplayName(member) {
      const u = member.user;
      const full = ((u.first_name || '') + ' ' + (u.last_name || '')).trim();
      return full || u.username;
    },

    // -- Meeting provenance banner --------------------------
    // The conversation payload comes in two shapes (see _meeting_payload in
    // chat/serializers.py). A list row carries no next_start key at all; a
    // single-conversation payload carries one and may set it to null. Absent
    // means "not computed here" and prints nothing, null is a real answer and
    // says so - announcing an empty schedule the server never looked up would
    // be a lie the banner cannot take back.
    meetingOccurrenceLabel(conversation) {
      const meeting = conversation && conversation.meeting;
      if (!meeting || !('next_start' in meeting)) return '';
      if (!meeting.next_start) return 'No upcoming occurrence';
      return this.formatDateTime(meeting.next_start);
    },

    copyMeetingJoinUrl(conversation) {
      const url = conversation && conversation.meeting && conversation.meeting.join_url;
      if (!url) return Promise.resolve();
      return navigator.clipboard.writeText(url).then(() => {
        if (window.AppAlert) window.AppAlert.success('Join link copied', { duration: 2000 });
      }).catch(() => {
        if (window.AppAlert) window.AppAlert.error('Failed to copy the link');
      });
    },

    autoResize(el) {
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 128) + 'px';
    },
  };
};
