// Voice room Alpine app. Reuses the chat mixins (messages, input, SSE, members,
// panels, bot, call) but is locked to a single conversation and owns the call.
// No sidebar, no conversation list: the room is one conversation, full screen.

function chatRoomApp(currentUserId, conversationId) {
  return {
    currentUserId: currentUserId,
    roomConversationId: conversationId,
    callRole: 'owner',
    speakingIds: {},
    pinnedKey: null,
    pinnedManually: false,
    callElapsed: '00:00',
    _callStartMs: null,
    _durationTimer: null,
    _audioCtx: null,
    _meterTimer: null,
    chatPrefs: { ...(window._chatPrefsCache || {}) },

    ...chatUiHelpersMixin(),
    ...chatConversationsMixin(),
    ...chatMessagesMixin(),
    ...chatInputMixin(),
    ...chatSseMixin(),
    ...chatMembersMixin(),
    ...chatPanelsMixin(),
    ...chatThreadsMixin(),
    ...chatBotMixin(),
    ...chatCallMixin(),
    ...chatMeetingHostMixin(),
    ...chatCallDiagnosticMixin(),
    ...chatRecorderMixin(),

    // Placed after the mixin spreads: chatCallMixin() declares its own
    // currentParticipantKey: null default, which would otherwise win.
    currentParticipantKey: `u:${currentUserId}`,

    async init() {
      this._initCallSounds?.();

      // Probes MediaRecorder support once so the mic button can hide itself
      // on browsers that cannot record.
      this.initRecorder();

      // Seed the active conversation from server-serialized data so the reused
      // conversation pane (header, info panel) shows the real name and members,
      // not the "Group" fallback. Fall back to a uuid-only stub if missing.
      let conv = null;
      const convEl = document.getElementById('room-conversation-data');
      if (convEl) {
        try { conv = JSON.parse(convEl.textContent); } catch (e) { conv = null; }
      }
      this.activeConversation = conv || { uuid: this.roomConversationId };

      const meetingEl = document.getElementById('room-meeting-data');
      if (meetingEl) {
        try { this.meeting = JSON.parse(meetingEl.textContent); } catch (e) { this.meeting = null; }
      }
      if (this.meeting) await this.loadLobby();

      // Announce room presence so the main tab flips Join <-> Return instantly,
      // without waiting on the heartbeat/SSE round-trip.
      try {
        this._roomChannel = new BroadcastChannel('chat-call');
        this._roomChannel.postMessage({ type: 'room-open', conversationId: this.roomConversationId });
        window.addEventListener('pagehide', () => {
          try { this._roomChannel.postMessage({ type: 'room-closed', conversationId: this.roomConversationId }); } catch (e) {}
        });
      } catch (e) { /* BroadcastChannel unsupported: fall back to server state */ }

      // Leave cleanly when the tab closes (existing beacon).
      window.addEventListener('pagehide', () => { if (this.inCall) this._leaveBeacon?.(); });

      // Load the conversation messages, then auto-join the call.
      await this.loadMessages();
      await this.startOrJoinCall();
      if (this.inCall) {
        this._startSpeakingMeter();
        this._startDurationTimer();
      }
    },

    // Lightweight speaking meter: sample local + remote streams ~10/s and flag
    // tiles whose normalized RMS crosses the threshold. Purely visual.
    _startSpeakingMeter() {
      if (this._meterTimer) return; // already running
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      this._audioCtx = new Ctx();
      const analysers = {}; // participant_key -> { analyser, data }

      const attach = (key, stream) => {
        if (!stream || analysers[key]) return;
        const src = this._audioCtx.createMediaStreamSource(stream);
        const analyser = this._audioCtx.createAnalyser();
        analyser.fftSize = 512;
        src.connect(analyser);
        analysers[key] = { analyser, data: new Uint8Array(analyser.frequencyBinCount) };
      };

      this._meterTimer = setInterval(() => {
        if (this._localStream) attach(this.currentParticipantKey, this._localStream);
        for (const id of Object.keys(this._peers || {})) {
          const el = this._peers[id].audioEl;
          if (el && el.srcObject) attach(id, el.srcObject);
        }
        // Prune analysers for peers that have departed; always keep local user
        const activeIds = new Set(Object.keys(this._peers || {}));
        activeIds.add(this.currentParticipantKey);
        for (const id of Object.keys(analysers)) {
          if (!activeIds.has(id)) delete analysers[id];
        }
        const next = {};
        for (const id of Object.keys(analysers)) {
          const { analyser, data } = analysers[id];
          analyser.getByteTimeDomainData(data);
          let sum = 0;
          for (let i = 0; i < data.length; i++) {
            const v = (data[i] - 128) / 128;
            sum += v * v;
          }
          const rms = Math.sqrt(sum / data.length);
          const muted = id === this.currentParticipantKey && this.isMuted;
          next[id] = !muted && window.chatIsSpeaking(rms);
        }
        this.speakingIds = next;
      }, 100);
    },

    _startDurationTimer() {
      if (this._durationTimer) return; // idempotent
      // Prefer the server-supplied start so all participants share the same clock.
      const serverTs = this.callSession && this.callSession.started_at;
      const start = serverTs ? new Date(serverTs).getTime() : Date.now();
      this._callStartMs = isNaN(start) ? Date.now() : start;
      this.callElapsed = this._formatDuration(Date.now() - this._callStartMs);
      this._durationTimer = setInterval(() => {
        this.callElapsed = this._formatDuration(Date.now() - this._callStartMs);
      }, 1000);
    },

    _stopDurationTimer() {
      if (this._durationTimer) { clearInterval(this._durationTimer); this._durationTimer = null; }
    },

    _formatDuration(ms) {
      return window.chatRoomFormatDuration(ms);
    },

    _stopSpeakingMeter() {
      if (this._meterTimer) { clearInterval(this._meterTimer); this._meterTimer = null; }
      if (this._audioCtx) { try { this._audioCtx.close(); } catch (e) {} this._audioCtx = null; }
    },

    leaveRoom() {
      return this.leaveCall().finally(() => {
        window.close();
        // window.close() is a no-op for a tab the script did not open (e.g. a
        // direct visit or refresh of the room URL); fall back to the chat list.
        setTimeout(() => { window.location.href = '/chat'; }, 100);
      });
    },

    isSpeaking(participantKey) {
      return !!this.speakingIds[participantKey];
    },

    remoteParticipants() {
      return this.callParticipants.filter((p) => p.participant_key !== this.currentParticipantKey);
    },

    selfParticipant() {
      return this.callParticipants.find((p) => p.participant_key === this.currentParticipantKey) || null;
    },

    gridColumns() {
      return Math.max(1, Math.ceil(Math.sqrt(this.remoteParticipants().length || 1)));
    },

    pinTile(participantKey) {
      // Click a tile to spotlight it; click the pinned tile again to return to
      // the grid. Any click marks the choice manual so auto-pin yields to it.
      this.pinnedKey = (this.pinnedKey === participantKey) ? null : participantKey;
      this.pinnedManually = true;
    },

    backToGrid() {
      this.pinnedKey = null;
      this.pinnedManually = true;
    },

    spotlightKey() {
      return window.chatCallSpotlightTarget(this.callParticipants, this.pinnedKey, this.pinnedManually);
    },

    isSpotlight() {
      return this.spotlightKey() != null;
    },

    spotlightParticipant() {
      const key = this.spotlightKey();
      return key == null ? null : this.callParticipants.find((p) => p.participant_key === key) || null;
    },

    stripParticipants() {
      // Everyone except the spotlighted participant, for the thumbnail strip.
      const key = this.spotlightKey();
      return this.callParticipants.filter((p) => p.participant_key !== key);
    },

    hasVideo(p) {
      if (p && p.participant_key === this.currentParticipantKey) return !!(this.cameraOn || this.sharing);
      return !!(p && p.media_state && (p.media_state.video || p.media_state.screen));
    },

    streamFor(participantKey) {
      if (participantKey === this.currentParticipantKey) return this.localVideoStream || null;
      return this.remoteStreams[participantKey] || null;
    },

    // No onCallParticipantUpdated override: the call mixin applies media_state,
    // and spotlightKey() derives the auto-pin from that live state, so a
    // sharer is spotlighted (or cleared) reactively without latching an event.

    onCallParticipantLeft(detail) {
      if (this.inCall && !window.chatCallEventForCurrentSession(detail, this.callSession)) return;
      if (detail.participant_key !== this.currentParticipantKey) this._playCallCue('peer-leave');
      this.callParticipants = this.callParticipants.filter((p) => p.participant_key !== detail.participant_key);
      this._closePeer(detail.participant_key);
      if (this.pinnedKey === detail.participant_key) {
        this.pinnedKey = null;
        this.pinnedManually = false;  // pin gone; allow auto-pin again
      }
    },
  };
}

window.chatRoomApp = chatRoomApp;
