// Voice room Alpine app. Reuses the chat mixins (messages, input, SSE, members,
// panels, bot, call) but is locked to a single conversation and owns the call.
// No sidebar, no conversation list: the room is one conversation, full screen.
function chatRoomApp(currentUserId, conversationId) {
  return {
    currentUserId: currentUserId,
    roomConversationId: conversationId,
    callRole: 'owner',
    roomParticipants: [],
    speakingIds: {},
    _audioCtx: null,
    _meterTimer: null,
    chatPrefs: { ...(window._chatPrefsCache || {}) },

    ...chatMessagesMixin(),
    ...chatInputMixin(),
    ...chatSseMixin(),
    ...chatMembersMixin(),
    ...chatPanelsMixin(),
    ...chatBotMixin(),
    ...chatCallMixin(),

    async init() {
      this._initCallSounds?.();

      // Lock the app to this conversation (no list, no switching). Seed
      // activeConversation so every mixin that reads it targets the room call.
      this.activeConversation = { uuid: this.roomConversationId };

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
    },

    // Lightweight speaking meter: sample local + remote streams ~10/s and flag
    // tiles whose normalized RMS crosses the threshold. Purely visual.
    _startSpeakingMeter() {
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

    _stopSpeakingMeter() {
      if (this._meterTimer) { clearInterval(this._meterTimer); this._meterTimer = null; }
      if (this._audioCtx) { try { this._audioCtx.close(); } catch (e) {} this._audioCtx = null; }
    },

    isSpeaking(userId) {
      return !!this.speakingIds[userId];
    },
  };
}

window.chatRoomApp = chatRoomApp;
