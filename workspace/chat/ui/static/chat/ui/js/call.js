// Chat audio calls: WebRTC mesh over SSE signaling.
//
// Pure helpers (testable via the node:vm loader) are declared as top-level
// functions and mirrored onto window. The Alpine mixin (chatCallMixin) holds
// the RTCPeerConnection wiring and is validated in a real browser.

function chatCallShouldOffer(selfId, newcomerId) {
  // Deterministic glare avoidance: an existing participant offers to a
  // newcomer. "Should I offer to this newcomer?" is true for everyone but
  // the newcomer themselves.
  return selfId !== newcomerId;
}

function chatCallMergeMediaState(current, patch) {
  return Object.assign({}, current || {}, patch || {});
}

function chatCallOtherParticipantIds(participants, selfId) {
  return (participants || [])
    .map((p) => p.user_id)
    .filter((id) => id !== selfId);
}

function chatCallEventForCurrentSession(detail, callSession) {
  // True when an incoming call event belongs to the session the mixin is
  // currently tracking (the call you are in, or the banner you are showing).
  // Guards against a different conversation's call events corrupting this one.
  return !!callSession && !!detail && detail.session_id === callSession.session_id;
}

window.chatCallShouldOffer = chatCallShouldOffer;
window.chatCallMergeMediaState = chatCallMergeMediaState;
window.chatCallOtherParticipantIds = chatCallOtherParticipantIds;
window.chatCallEventForCurrentSession = chatCallEventForCurrentSession;

window.chatCallMixin = function chatCallMixin() {
  return {
    // -- Call state ------------------------------------------
    callSession: null,           // serialized state of the active call, or null
    callParticipants: [],        // [{user_id, display_name, media_state}]
    inCall: false,               // am I currently joined?
    isMuted: false,
    joiningCall: false,
    callRole: 'owner',          // 'owner' (room tab) | 'observer' (main tab)
    _peers: {},                  // user_id -> { pc, audioEl }
    _localStream: null,
    _iceServers: [],
    _heartbeatTimer: null,

    _csrf() {
      return (typeof getCSRFToken === 'function') ? getCSRFToken()
        : (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';
    },

    _loadIceServers() {
      if (this._iceServers.length) return this._iceServers;
      const el = document.getElementById('call-ice-servers-data');
      if (el) {
        try { this._iceServers = JSON.parse(el.textContent); } catch (e) { this._iceServers = []; }
      }
      return this._iceServers;
    },

    _initCallSounds() {
      if (!window.chatCallSounds) return;
      const el = document.getElementById('call-sounds-enabled-data');
      let on = true;
      if (el) {
        try { on = !!JSON.parse(el.textContent); } catch (e) { on = true; }
      }
      window.chatCallSounds.setEnabled(on);
    },

    _playCallCue(event) {
      if (window.chatCallSounds && window.chatCallSoundCue) {
        window.chatCallSounds.play(window.chatCallSoundCue(event, this.isMuted));
      }
    },

    callBannerVisible() {
      return !!this.callSession && !this.inCall;
    },

    isInCall() {
      return this.inCall;
    },

    _mediaState() {
      return { audio: !this.isMuted };
    },

    // -- Lifecycle: join / leave -----------------------------
    async startOrJoinCall() {
      // The main tab is an observer: it never owns the mic. Joining means
      // opening the dedicated room tab instead of capturing media here.
      if (!window.chatCallShouldOwnMedia(this.callRole)) {
        this.openCallRoom(this.activeConversation && this.activeConversation.uuid);
        return;
      }
      if (!this.activeConversation || this.joiningCall) return;
      this.joiningCall = true;
      const convId = this.activeConversation.uuid;
      try {
        this._localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (e) {
        this.joiningCall = false;
        if (typeof this.showAlert === 'function') {
          this.showAlert('error', 'Microphone permission is required to join a call.');
        }
        return;
      }
      let resp;
      try {
        resp = await fetch(`/api/v1/chat/conversations/${convId}/call/join`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this._csrf() },
        });
      } catch (e) {
        this._teardownLocal();
        this.joiningCall = false;
        return;
      }
      if (resp.status === 409) {
        this._teardownLocal();
        this.joiningCall = false;
        if (typeof this.showAlert === 'function') this.showAlert('warning', 'This call is full.');
        return;
      }
      const data = await resp.json();
      this._iceServers = data.ice_servers || [];
      this.callSession = data.state;
      this.callParticipants = data.state.participants || [];
      this.inCall = true;
      this.joiningCall = false;
      this._playCallCue('join');

      // As the newcomer, wait for existing participants to offer; just create
      // peer slots for everyone already here.
      for (const id of window.chatCallOtherParticipantIds(this.callParticipants, this.currentUserId)) {
        this._ensurePeer(id, /* initiateOffer */ false);
      }
      this._startHeartbeat();

      // The "Call started" system message is authored by us, and the SSE stream
      // excludes our own messages, so it is never pushed back to us. Refresh the
      // message list so the initiator sees it too, like the other participants.
      if (typeof this._refreshMessagesPreservingScroll === 'function' && this.activeConversation) {
        this._refreshMessagesPreservingScroll();
      }
    },

    async leaveCall() {
      // Leave the call's own conversation, captured before we clear the session
      // (you may be viewing a different conversation while in the call).
      const convId = this.callSession && this.callSession.conversation_id;
      this._playCallCue('leave');
      this._stopHeartbeat();
      for (const id of Object.keys(this._peers)) this._closePeer(Number(id));
      this._teardownLocal();
      this.inCall = false;
      this.isMuted = false;
      this.callParticipants = [];
      this.callSession = null;
      // Notify the main-tab observer synchronously, before the fetch completes.
      // Posting here (while the page is still alive) ensures the main tab
      // receives the room-closed signal even if the tab closes before pagehide.
      if (this._roomChannel) {
        try {
          this._roomChannel.postMessage({ type: 'room-closed', conversationId: convId });
        } catch (e) { /* best effort */ }
      }
      if (convId) {
        try {
          await fetch(`/api/v1/chat/conversations/${convId}/call/leave`, {
            method: 'POST',
            headers: { 'X-CSRFToken': this._csrf() },
            keepalive: true,
          });
        } catch (e) { /* best effort */ }
      }
      // Hanging up clears callSession, but the call may still be ongoing with
      // the others. Re-sync the banner for the conversation in view so a
      // still-active call becomes joinable again right away, instead of waiting
      // on the SSE round-trip that re-advertises it.
      this._syncCallBanner();
    },

    openCallRoom(conversationId) {
      const convId = conversationId
        || (this.callSession && this.callSession.conversation_id)
        || (this.activeConversation && this.activeConversation.uuid);
      if (!convId) return;
      const url = window.chatCallRoomUrl(convId);
      const name = window.chatCallRoomTabName(convId);
      const win = window.open(url, name);
      if (win) {
        try { win.focus(); } catch (e) { /* background-tab focus may be denied */ }
      } else if (typeof this.showAlert === 'function') {
        this.showAlert('warning', 'Allow pop-ups to open the voice room.');
      }
    },

    callBannerAction() {
      return window.chatCallBannerAction(
        !!this.callSession,
        this.callParticipants,
        this.currentUserId,
      );
    },

    // Best-effort clean leave when the page is unloading (navigation to another
    // module, reload, tab close). keepalive lets the POST outlive the page and,
    // unlike sendBeacon, carries the CSRF header the endpoint requires.
    _leaveBeacon() {
      const convId = this.callSession && this.callSession.conversation_id;
      if (!convId) return;
      try {
        fetch(`/api/v1/chat/conversations/${convId}/call/leave`, {
          method: 'POST',
          headers: { 'X-CSRFToken': this._csrf() },
          keepalive: true,
        });
      } catch (e) { /* page is going away */ }
    },

    toggleMute() {
      this.isMuted = !this.isMuted;
      if (this._localStream) {
        this._localStream.getAudioTracks().forEach((t) => { t.enabled = !this.isMuted; });
      }
      this._sendHeartbeat(); // pushes the new media_state immediately
      this._playCallCue('toggle-mute');
    },

    _teardownLocal() {
      if (this._localStream) {
        this._localStream.getTracks().forEach((t) => t.stop());
        this._localStream = null;
      }
    },

    // -- Heartbeat -------------------------------------------
    _startHeartbeat() {
      this._sendHeartbeat();
      this._heartbeatTimer = setInterval(() => this._sendHeartbeat(), 5000);
    },
    _stopHeartbeat() {
      if (this._heartbeatTimer) { clearInterval(this._heartbeatTimer); this._heartbeatTimer = null; }
    },
    async _sendHeartbeat() {
      // Always target the call's own conversation, not the one being viewed:
      // you stay in the call while browsing elsewhere, so a heartbeat to the
      // active conversation would miss the call and let the sweep reap you.
      const convId = this.callSession && this.callSession.conversation_id;
      if (!this.inCall || !convId) return;
      try {
        await fetch(`/api/v1/chat/conversations/${convId}/call/heartbeat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this._csrf() },
          body: JSON.stringify({ media_state: this._mediaState() }),
        });
      } catch (e) { /* transient */ }
    },

    // -- Peer connections ------------------------------------
    _ensurePeer(peerId, initiateOffer) {
      if (this._peers[peerId]) return this._peers[peerId];
      const pc = new RTCPeerConnection({ iceServers: this._loadIceServers() });
      const audioEl = document.createElement('audio');
      audioEl.autoplay = true;
      audioEl.dataset.peer = String(peerId);
      document.body.appendChild(audioEl);

      if (this._localStream) {
        this._localStream.getTracks().forEach((t) => pc.addTrack(t, this._localStream));
      }
      pc.ontrack = (ev) => { audioEl.srcObject = ev.streams[0]; };
      pc.onicecandidate = (ev) => {
        if (ev.candidate) this._sendSignal(peerId, { type: 'ice', candidate: ev.candidate });
      };
      pc.oniceconnectionstatechange = () => {
        if (['failed', 'closed', 'disconnected'].includes(pc.iceConnectionState)) {
          // Let cleanup / participant_left handle teardown.
        }
      };
      this._peers[peerId] = { pc, audioEl };
      if (initiateOffer) {
        pc.createOffer()
          .then((offer) => pc.setLocalDescription(offer)
            .then(() => this._sendSignal(peerId, { type: 'offer', sdp: pc.localDescription })));
      }
      return this._peers[peerId];
    },

    _closePeer(peerId) {
      const peer = this._peers[peerId];
      if (!peer) return;
      try { peer.pc.close(); } catch (e) { /* ignore */ }
      if (peer.audioEl && peer.audioEl.parentNode) peer.audioEl.parentNode.removeChild(peer.audioEl);
      delete this._peers[peerId];
    },

    async _sendSignal(toUserId, signal) {
      if (!this.activeConversation) return;
      try {
        await fetch(`/api/v1/chat/conversations/${this.activeConversation.uuid}/call/signal`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this._csrf() },
          body: JSON.stringify({ to_user_id: toUserId, signal }),
        });
      } catch (e) { /* transient */ }
    },

    // -- SSE handlers (wired in index.html) ------------------
    onCallStarted(detail) {
      if (!this.activeConversation || detail.conversation_id !== this.activeConversation.uuid) return;
      if (this.inCall) return;
      // Show the "call in progress" banner; fetch authoritative state.
      this._refreshCallState();
    },
    onCallEnded(detail) {
      if (this.callSession && detail && detail.session_id !== this.callSession.session_id) return;
      // Single active call per conversation: any call_ended clears the banner
      // and tears down our local peer connections if we were in the call.
      this.callSession = null;
      this.callParticipants = [];
      if (this.inCall) this.leaveCall();
      if (typeof this._refreshMessagesPreservingScroll === 'function' && this.activeConversation) {
        this._refreshMessagesPreservingScroll();
      }
    },
    onCallParticipantJoined(detail) {
      if (!this.inCall) { this._refreshCallState(); return; }
      if (!window.chatCallEventForCurrentSession(detail, this.callSession)) return;
      const id = detail.user_id;
      if (id === this.currentUserId) return;
      this._playCallCue('peer-join');
      if (!this.callParticipants.find((p) => p.user_id === id)) {
        this.callParticipants.push({ user_id: id, display_name: detail.display_name, media_state: detail.media_state });
      }
      // Existing participant (me) offers to the newcomer.
      if (window.chatCallShouldOffer(this.currentUserId, id)) {
        this._ensurePeer(id, /* initiateOffer */ true);
      }
    },
    onCallParticipantLeft(detail) {
      if (this.inCall && !window.chatCallEventForCurrentSession(detail, this.callSession)) return;
      if (this.inCall && detail.user_id !== this.currentUserId) {
        this._playCallCue('peer-leave');
      }
      this.callParticipants = this.callParticipants.filter((p) => p.user_id !== detail.user_id);
      this._closePeer(detail.user_id);
      if (!this.inCall) this._refreshCallState();
    },
    onCallParticipantUpdated(detail) {
      if (this.inCall && !window.chatCallEventForCurrentSession(detail, this.callSession)) return;
      const p = this.callParticipants.find((x) => x.user_id === detail.user_id);
      if (p) p.media_state = detail.media_state;
    },
    async onCallSignal(detail) {
      if (!this.inCall) return;
      const fromId = detail.from_user_id;
      const signal = detail.signal || {};
      const peer = this._ensurePeer(fromId, /* initiateOffer */ false);
      const pc = peer.pc;
      try {
        if (signal.type === 'offer') {
          await pc.setRemoteDescription(signal.sdp);
          const answer = await pc.createAnswer();
          await pc.setLocalDescription(answer);
          this._sendSignal(fromId, { type: 'answer', sdp: pc.localDescription });
        } else if (signal.type === 'answer') {
          await pc.setRemoteDescription(signal.sdp);
        } else if (signal.type === 'ice' && signal.candidate) {
          await pc.addIceCandidate(signal.candidate);
        }
      } catch (e) {
        // Negotiation can fail on browser quirks or out-of-order signals; keep
        // the call alive instead of raising an unhandled promise rejection.
        console.warn('WebRTC signal handling failed:', signal.type, e);
      }
    },

    // Refresh the banner for the conversation now in view. Called whenever the
    // active conversation changes (including F5), so an already-ongoing call is
    // joinable even though no SSE event fired while we were away.
    _syncCallBanner() {
      // Never clobber the call we are actually in while browsing other convs.
      if (this.inCall) return;
      if (!this.activeConversation) {
        this.callSession = null;
        this.callParticipants = [];
        return;
      }
      this._refreshCallState();
    },

    async _refreshCallState() {
      if (!this.activeConversation) return;
      try {
        const resp = await fetch(`/api/v1/chat/conversations/${this.activeConversation.uuid}/call`);
        const data = await resp.json();
        this.callSession = data.active ? data : null;
        this.callParticipants = data.active ? (data.participants || []) : [];
      } catch (e) { /* transient */ }
    },
  };
};
