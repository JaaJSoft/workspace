// Voice room Alpine app. Reuses the chat mixins (messages, input, SSE, members,
// panels, bot, call) but is locked to a single conversation and owns the call.
// No sidebar, no conversation list: the room is one conversation, full screen.

/**
 * Format a duration in milliseconds as mm:ss, or h:mm:ss when >= 1 hour.
 * Negative values are clamped to 0. Pure function - no side effects.
 * @param {number} ms
 * @returns {string}
 */
function chatRoomFormatDuration(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const s = total % 60;
  const m = Math.floor(total / 60) % 60;
  const h = Math.floor(total / 3600);
  const pad = (n) => String(n).padStart(2, '0');
  if (h > 0) {
    return `${h}:${pad(m)}:${pad(s)}`;
  }
  return `${pad(m)}:${pad(s)}`;
}
window.chatRoomFormatDuration = chatRoomFormatDuration;

function chatRoomApp(currentUserId, conversationId) {
  return {
    currentUserId: currentUserId,
    roomConversationId: conversationId,
    callRole: 'owner',
    roomParticipants: [],
    speakingIds: {},
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
    ...chatBotMixin(),
    ...chatCallMixin(),
    ...chatCallDiagnosticMixin(),

    async init() {
      this._initCallSounds?.();

      // Seed the active conversation from server-serialized data so the reused
      // conversation pane (header, info panel) shows the real name and members,
      // not the "Group" fallback. Fall back to a uuid-only stub if missing.
      let conv = null;
      const convEl = document.getElementById('room-conversation-data');
      if (convEl) {
        try { conv = JSON.parse(convEl.textContent); } catch (e) { conv = null; }
      }
      this.activeConversation = conv || { uuid: this.roomConversationId };

      // Announce room presence so the main tab flips Join <-> Return instantly,
      // without waiting on the heartbeat/SSE round-trip.
      try {
        this._roomChannel = new BroadcastChannel('chat-call');
        this._roomChannel.postMessage({ type: 'room-open', conversationId: this.roomConversationId });
        window.addEventListener('pagehide', () => {
          try { this._roomChannel.postMessage({ type: 'room-closed', conversationId: this.roomConversationId }); } catch (e) {}
        });
      } catch (e) { /* BroadcastChannel unsupported: fall back to server state */ }

      // Seed the participants grid from the embedded server data.
      const seedEl = document.getElementById('room-participants-data');
      if (seedEl) {
        try { this.roomParticipants = JSON.parse(seedEl.textContent); }
        catch (e) { this.roomParticipants = []; }
      }

      // Leave cleanly when the tab closes (existing beacon).
      window.addEventListener('pagehide', () => { if (this.inCall) this._leaveBeacon?.(); });

      // Load the conversation messages, then auto-join the call.
      await this.loadMessages(this.roomConversationId);
      await this.startOrJoinCall();
      this._startSpeakingMeter();
      this._startDurationTimer();
    },

    // Lightweight speaking meter: sample local + remote streams ~10/s and flag
    // tiles whose normalized RMS crosses the threshold. Purely visual.
    _startSpeakingMeter() {
      if (this._meterTimer) return; // already running
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      this._audioCtx = new Ctx();
      const analysers = {}; // user_id -> { analyser, data }

      const attach = (userId, stream) => {
        if (!stream || analysers[userId]) return;
        const src = this._audioCtx.createMediaStreamSource(stream);
        const analyser = this._audioCtx.createAnalyser();
        analyser.fftSize = 512;
        src.connect(analyser);
        analysers[userId] = { analyser, data: new Uint8Array(analyser.frequencyBinCount) };
      };

      this._meterTimer = setInterval(() => {
        if (this._localStream) attach(this.currentUserId, this._localStream);
        for (const id of Object.keys(this._peers || {})) {
          const el = this._peers[id].audioEl;
          if (el && el.srcObject) attach(Number(id), el.srcObject);
        }
        // Prune analysers for peers that have departed; always keep local user
        const activeIds = new Set(Object.keys(this._peers || {}));
        activeIds.add(String(this.currentUserId));
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
          const muted = id === String(this.currentUserId) && this.isMuted;
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

    isSpeaking(userId) {
      return !!this.speakingIds[userId];
    },

    remoteParticipants() {
      return this.callParticipants.filter(p => p.user_id !== this.currentUserId);
    },

    selfParticipant() {
      return this.callParticipants.find(p => p.user_id === this.currentUserId) || null;
    },

    gridColumns() {
      return Math.max(1, Math.ceil(Math.sqrt(this.remoteParticipants().length || 1)));
    },
  };
}

window.chatRoomApp = chatRoomApp;
