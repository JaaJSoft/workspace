// The host side of the meeting page: the call stage on a meeting scope, the
// lobby and lock controls, and the meeting chat. Composes the same call and
// chat mixins the guest page composes, with the host transport seam.

// Tailwind's md, which is where the chat pane stops being a slide-over.
const CHAT_MEETING_PANE_ON_SCREEN = '(min-width: 768px)';

function chatMeetingHostApp(currentUserId) {
  const call = chatCallMixin();
  const stage = chatCallStageMixin();
  const host = chatMeetingHostMixin();
  const chat = chatMeetingChatMixin();
  const input = chatInputMixin();
  const uiHelpers = chatUiHelpersMixin();

  return {
    ...uiHelpers,
    ...input,
    ...call,
    ...stage,
    ...host,
    ...chat,

    currentUserId,
    currentParticipantKey: `u:${currentUserId}`,
    callRole: 'room',
    chatOpen: false,
    // Below md the pane is a slide-over, so a line can land with nobody
    // looking; the stage's dock is the only place the count is drawn.
    unreadMessages: 0,
    chatPrefs: { ...(window._chatPrefsCache || {}) },
    activeConversation: null,

    async init() {
      this._initCallSounds?.();
      const el = document.getElementById('meeting-data');
      try { this.meeting = el ? JSON.parse(el.textContent) : null; } catch (e) { this.meeting = null; }
      // Every call below is addressed by the meeting uuid, so there is nothing
      // this page can do without it.
      if (!this.meeting) return;
      // The call mixin gates on "is there something to talk to"; the meeting
      // uuid stands in for the conversation uuid it expects.
      this.activeConversation = { uuid: this.meeting.uuid, kind: 'group', members: [] };
      await this.loadLobby();
      this._startLobbyRefresh();
      await this.loadMeetingMessages();
      await this._refreshCallState();
      window.addEventListener('pagehide', () => { if (this.inCall) this._leaveBeacon(); });
    },

    destroy() {
      this._stopLobbyRefresh?.();
      this._stopDurationTimer();
      for (const mixin of [uiHelpers, input, call, stage, host, chat]) mixin.destroy?.call(this);
    },

    meetingTitle() { return (this.meeting && this.meeting.title) || 'Meeting'; },
    summaryLine() {
      if (!this.meeting || !this.meeting.next_start) return '';
      const d = new Date(this.meeting.next_start);
      return isNaN(d) ? '' : d.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
    },
    toggleChat() {
      this.chatOpen = !this.chatOpen;
      if (!this.chatOpen) return;
      this.unreadMessages = 0;
      this.scrollMeetingChatToBottom();
    },
    // At md and above the pane is always on screen (hidden md:flex) and
    // nothing ever sets chatOpen there, so "not opened" cannot stand for
    // "not seen".
    _meetingChatPaneHidden() {
      return !this.chatOpen && !window.matchMedia(CHAT_MEETING_PANE_ON_SCREEN).matches;
    },
    _onMeetingMessageArrived(message) {
      const key = message.author && message.author.participant_key;
      if (this._meetingChatPaneHidden() && key !== this.currentParticipantKey) {
        this.unreadMessages += 1;
      }
    },
    copyJoinUrl() {
      return navigator.clipboard.writeText(this.meeting.join_url)
        .then(() => window.AppAlert?.success('Join link copied', { duration: 2000 }));
    },

    // -- Call transport seam (see call.js) --
    _callEndpoint(action) {
      const base = `/api/v1/chat/meetings/${this.meeting.uuid}/call`;
      return action ? `${base}/${action}` : base;
    },
    _leaveTarget() { return this.meeting ? this.meeting.uuid : null; },
    _syncCallBanner() {},
    onCallStarted(detail) {
      if (!detail || detail.meeting_id !== this.meeting.uuid) return;
      return this._refreshCallState();
    },
    async _refreshCallState() {
      const resp = await fetch(this._callEndpoint(''), { headers: this._callHeaders() });
      if (!resp.ok) return;
      const data = await resp.json();
      this.callSession = data.active ? data : null;
      this.callParticipants = data.active ? (data.participants || []) : [];
    },
    async joinMeetingCall() {
      await this.startOrJoinCall();
      if (this.inCall) this._startDurationTimer();
    },
    async onCallEnded(detail) {
      if (this.callSession && detail && detail.session_id !== this.callSession.session_id) return;
      this.callSession = null;
      this.callParticipants = [];
      await this.leaveCall();
      this._stopDurationTimer();
    },
    leaveRoom() {
      return this.leaveCall().finally(() => { this._stopDurationTimer(); });
    },

    // -- Meeting chat seam --
    _meetingMessagesUrl(cursor) {
      const base = `/api/v1/chat/meetings/${this.meeting.uuid}/messages`;
      return cursor ? `${base}?before=${cursor}` : base;
    },
    _meetingMessageHeaders() { return { 'X-CSRFToken': this._csrf() }; },
    meetingId() { return this.meeting ? this.meeting.uuid : null; },
    _canDeleteMeetingMessages() { return true; },
    isBotMessage() { return false; },
  };
}

window.chatMeetingHostApp = chatMeetingHostApp;
