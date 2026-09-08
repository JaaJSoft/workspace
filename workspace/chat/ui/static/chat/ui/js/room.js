// Voice room Alpine app. Reuses the chat mixins (messages, input, SSE, members,
// panels, bot, call) but is locked to a single conversation and owns the call.
// No sidebar, no conversation list: the room is one conversation, full screen.

function chatRoomApp(currentUserId, conversationId) {
  // Held so destroy() below can reach this mixin's own teardown by name.
  const threads = chatThreadsMixin();

  return {
    currentUserId: currentUserId,
    roomConversationId: conversationId,
    callRole: 'owner',
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
    ...threads,
    ...chatBotMixin(),
    ...chatCallMixin(),
    ...chatCallStageMixin(),
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

    // Alpine calls exactly one destroy(), and object spread would let the last
    // mixin that declares one win in silence. The room's teardown is listed
    // here, after the spreads, so adding a mixin with its own destroy cannot
    // quietly drop another's.
    destroy() {
      this._cancelMessagesRetry?.();
      threads.destroy?.call(this);
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
  };
}

window.chatRoomApp = chatRoomApp;
