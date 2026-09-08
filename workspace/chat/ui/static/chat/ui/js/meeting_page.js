// The host side of the meeting page: the call stage on a meeting scope, the
// lobby and lock controls, and the meeting chat. Composes the same call and
// chat mixins the guest page composes, with the host transport seam.

// Tailwind's md, which is where the chat pane stops being a slide-over.
const CHAT_MEETING_PANE_ON_SCREEN = '(min-width: 768px)';

function chatMeetingHostApp(currentUserId) {
  const call = chatCallMixin();
  const host = chatMeetingHostMixin();
  const chat = chatMeetingChatMixin();
  const input = chatInputMixin();
  const uiHelpers = chatUiHelpersMixin();

  return {
    ...uiHelpers,
    ...input,
    ...call,
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
    speakingIds: {},
    pinnedKey: null,
    pinnedManually: false,
    callElapsed: '00:00',
    _callStartMs: null,
    _durationTimer: null,
    activeConversation: null,

    async init() {
      this._initCallSounds?.();
      const el = document.getElementById('meeting-data');
      try { this.meeting = el ? JSON.parse(el.textContent) : null; } catch (e) { this.meeting = null; }
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
      for (const mixin of [uiHelpers, input, call, host, chat]) mixin.destroy?.call(this);
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
    _canDeleteMeetingMessages() { return true; },
    isBotMessage() { return false; },

    // -- Duration and stage helpers (same bodies as room.js) --
    _startDurationTimer() {
      if (this._durationTimer) return;
      const serverTs = this.callSession && this.callSession.started_at;
      const start = serverTs ? new Date(serverTs).getTime() : Date.now();
      this._callStartMs = isNaN(start) ? Date.now() : start;
      this.callElapsed = window.chatRoomFormatDuration(Date.now() - this._callStartMs);
      this._durationTimer = setInterval(() => {
        this.callElapsed = window.chatRoomFormatDuration(Date.now() - this._callStartMs);
      }, 1000);
    },
    _stopDurationTimer() {
      if (this._durationTimer) { clearInterval(this._durationTimer); this._durationTimer = null; }
    },
    isSpeaking(key) { return !!this.speakingIds[key]; },
    remoteParticipants() { return this.callParticipants.filter((p) => p.participant_key !== this.currentParticipantKey); },
    selfParticipant() { return this.callParticipants.find((p) => p.participant_key === this.currentParticipantKey) || null; },
    gridColumns() { return Math.max(1, Math.ceil(Math.sqrt(this.remoteParticipants().length || 1))); },
    pinTile(key) { this.pinnedKey = (this.pinnedKey === key) ? null : key; this.pinnedManually = true; },
    backToGrid() { this.pinnedKey = null; this.pinnedManually = true; },
    spotlightKey() { return window.chatCallSpotlightTarget(this.callParticipants, this.pinnedKey, this.pinnedManually); },
    isSpotlight() { return this.spotlightKey() != null; },
    spotlightParticipant() { const k = this.spotlightKey(); return k == null ? null : this.callParticipants.find((p) => p.participant_key === k) || null; },
    stripParticipants() { const k = this.spotlightKey(); return this.callParticipants.filter((p) => p.participant_key !== k); },
    hasVideo(p) {
      if (p && p.participant_key === this.currentParticipantKey) return !!(this.cameraOn || this.sharing);
      return !!(p && p.media_state && (p.media_state.video || p.media_state.screen));
    },
    streamFor(key) {
      if (key === this.currentParticipantKey) return this.localVideoStream || null;
      return this.remoteStreams[key] || null;
    },
    onCallParticipantLeft(detail) {
      if (this.inCall && !window.chatCallEventForCurrentSession(detail, this.callSession)) return;
      if (detail.participant_key !== this.currentParticipantKey) this._playCallCue('peer-leave');
      this.callParticipants = this.callParticipants.filter((p) => p.participant_key !== detail.participant_key);
      this._closePeer(detail.participant_key);
      if (this.pinnedKey === detail.participant_key) { this.pinnedKey = null; this.pinnedManually = false; }
    },
  };
}

window.chatMeetingHostApp = chatMeetingHostApp;
