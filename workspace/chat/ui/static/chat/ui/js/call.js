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

function chatCallMediaState(isMuted, cameraOn, sharing) {
  // Shape pushed by the heartbeat and read by remote tiles. audio is inverted
  // mute; video = camera on; screen = screen sharing. Camera and screen are
  // mutually exclusive at the call layer, but represented as distinct flags so
  // the UI can show the right icon and trigger auto-pin on screen.
  return { audio: !isMuted, video: !!cameraOn, screen: !!sharing };
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

function chatCallShouldDriveIceRestart(selfId, peerId) {
  // Glare avoidance for a mid-call ICE restart. At join time the existing
  // participant offers to the newcomer (chatCallShouldOffer), but on failure
  // both peers are existing participants, so that rule can't pick a side. The
  // lower user_id drives the restart offer; the other side only answers. Exactly
  // one side ever initiates, so the two never offer at once.
  return selfId < peerId;
}

function chatCallIceRestartDelay(state, attempts) {
  // Delay (ms) before attempting an ICE restart. 'disconnected' gets a 3s grace
  // floor because the state frequently self-recovers; 'failed' acts immediately
  // on the first attempt. Between attempts, exponential backoff 0 -> 2000 -> 4000.
  const graceFloor = state === 'disconnected' ? 3000 : 0;
  const backoff = attempts === 0 ? 0 : 2000 * Math.pow(2, attempts - 1);
  return Math.max(graceFloor, backoff);
}

window.chatCallShouldOffer = chatCallShouldOffer;
window.chatCallMergeMediaState = chatCallMergeMediaState;
window.chatCallMediaState = chatCallMediaState;
window.chatCallOtherParticipantIds = chatCallOtherParticipantIds;
window.chatCallEventForCurrentSession = chatCallEventForCurrentSession;
window.chatCallShouldDriveIceRestart = chatCallShouldDriveIceRestart;
window.chatCallIceRestartDelay = chatCallIceRestartDelay;

// Per-incident cap on ICE restart attempts. After this many tries fail, the
// client stops and falls back to the existing server-side reap (heartbeat
// expiry -> end_stale_calls -> call_participant_left). Reset to 0 on recovery,
// so the budget is per failure incident, not per call.
const MAX_ICE_RESTARTS = 3;

window.chatCallMixin = function chatCallMixin() {
  return {
    // -- Call state ------------------------------------------
    callSession: null,           // serialized state of the active call, or null
    callParticipants: [],        // [{user_id, display_name, media_state}]
    inCall: false,               // am I currently joined?
    isMuted: false,
    joiningCall: false,
    callRole: 'owner',          // 'owner' (room tab) | 'observer' (main tab)
    _peers: {},                  // user_id -> { pc, audioEl, remoteStream, videoSender }
    _localStream: null,
    cameraOn: false,
    sharing: false,
    remoteStreams: {},           // user_id -> MediaStream (audio+video)
    localVideoStream: null,      // MediaStream wrapping the current outgoing video track
    _localVideoTrack: null,      // the live camera or screen track, or null
    _videoRequestToken: 0,       // bumped per capture request and on teardown to cancel stale awaits
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
      return window.chatCallMediaState(this.isMuted, this.cameraOn, this.sharing);
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
      // Stop the room's speaking meter and duration timer if present (room tab
      // only; optional chaining makes this a no-op on the main observer tab).
      // Covers both the explicit Leave and the call_ended path, which both route
      // through leaveCall.
      this._stopSpeakingMeter?.();
      this._stopDurationTimer?.();
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

    async toggleCamera() {
      if (this.cameraOn) {
        this._stopLocalVideo();
        this.cameraOn = false;
        this._sendHeartbeat();
        return;
      }
      const token = ++this._videoRequestToken;
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { width: { ideal: 1280 }, height: { ideal: 720 } },
        });
      } catch (e) {
        if (typeof this.showAlert === 'function') {
          this.showAlert('error', 'Camera permission is required to turn on video.');
        }
        return;
      }
      // The user may have toggled again or left while the permission prompt was
      // open: a newer request (or teardown) bumped the token. Discard this stale
      // stream instead of attaching a track nobody asked for anymore.
      if (token !== this._videoRequestToken) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      // Camera and screen are exclusive: starting the camera stops any share.
      if (this.sharing) this.sharing = false;
      this._setLocalVideoTrack(stream.getVideoTracks()[0]);
      this.cameraOn = true;
      this._sendHeartbeat();
    },

    async toggleScreenShare() {
      if (this.sharing) {
        this._stopLocalVideo();
        this.sharing = false;
        this._sendHeartbeat();
        return;
      }
      const token = ++this._videoRequestToken;
      let stream;
      try {
        stream = await navigator.mediaDevices.getDisplayMedia({ video: true });
      } catch (e) {
        // User cancelled the picker, or permission denied: stay as we were.
        return;
      }
      // A newer request or a teardown superseded us while the picker was open.
      if (token !== this._videoRequestToken) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      const track = stream.getVideoTracks()[0];
      // The browser's own "Stop sharing" ends the track: revert cleanly.
      track.addEventListener('ended', () => {
        if (this.sharing) {
          this._stopLocalVideo();
          this.sharing = false;
          this._sendHeartbeat();
        }
      });
      // Exclusive with the camera.
      if (this.cameraOn) this.cameraOn = false;
      this._setLocalVideoTrack(track);
      this.sharing = true;
      this._sendHeartbeat();
    },

    // Route a new outgoing video track (camera or screen) to every peer and the
    // self-view. Stops the previous track so devices/captures are released.
    _setLocalVideoTrack(track) {
      const prev = this._localVideoTrack;
      this._localVideoTrack = track || null;
      for (const id of Object.keys(this._peers)) {
        const sender = this._peers[id].videoSender;
        if (sender) sender.replaceTrack(track || null);
      }
      this.localVideoStream = track ? new MediaStream([track]) : null;
      if (prev && prev !== track) prev.stop();
    },

    _stopLocalVideo() {
      this._setLocalVideoTrack(null);
    },

    _teardownLocal() {
      // Cancel any in-flight getUserMedia/getDisplayMedia: its post-await guard
      // sees the bumped token and discards the stream rather than reattaching.
      this._videoRequestToken++;
      if (this._localVideoTrack) {
        try { this._localVideoTrack.stop(); } catch (e) { /* ignore */ }
        this._localVideoTrack = null;
      }
      this.localVideoStream = null;
      this.cameraOn = false;
      this.sharing = false;
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

      // One MediaStream per peer holds both audio and video tracks. The hidden
      // <audio> element plays sound; the participant's <video> tile shows the
      // same stream (muted, so audio is not doubled).
      const remoteStream = new MediaStream();

      if (this._localStream) {
        this._localStream.getTracks().forEach((t) => pc.addTrack(t, this._localStream));
      }
      // Pre-negotiate the video lane up-front (track null). Every later camera/
      // screen toggle is a replaceTrack on this sender - no renegotiation, so no
      // mesh glare and no perfect-negotiation handshake.
      const videoTransceiver = pc.addTransceiver('video', { direction: 'sendrecv' });
      const videoSender = videoTransceiver.sender;
      // If we already have an outgoing video track (joined while camera/screen
      // was on), attach it to this new peer immediately.
      if (this._localVideoTrack) {
        videoSender.replaceTrack(this._localVideoTrack);
      }

      pc.ontrack = (ev) => {
        remoteStream.addTrack(ev.track);
        if (ev.track.kind === 'audio') {
          audioEl.srcObject = remoteStream;
        }
        // Reassign (not mutate) so Alpine reacts and tiles bind the stream.
        this.remoteStreams = Object.assign({}, this.remoteStreams, { [peerId]: remoteStream });
      };
      pc.onicecandidate = (ev) => {
        if (ev.candidate) this._sendSignal(peerId, { type: 'ice', candidate: ev.candidate });
      };
      pc.oniceconnectionstatechange = () => {
        const peer = this._peers[peerId];
        if (!peer) return;
        const state = pc.iceConnectionState;
        if (state === 'connected' || state === 'completed') {
          // Recovered: cancel any pending restart and refill the retry budget so
          // a later, unrelated blip gets a fresh set of attempts.
          peer.iceRestartAttempts = 0;
          if (peer.iceRestartTimer) { clearTimeout(peer.iceRestartTimer); peer.iceRestartTimer = null; }
        } else if (state === 'failed' || state === 'disconnected') {
          // Attempt a client-side ICE restart instead of waiting on the ~1 min
          // server reap. Only the deterministic driver actually initiates.
          this._scheduleIceRestart(peerId);
        }
        // 'closed': teardown is handled by _closePeer / participant_left.
      };
      this._peers[peerId] = { pc, audioEl, remoteStream, videoSender, iceRestartAttempts: 0, iceRestartTimer: null };
      if (initiateOffer) {
        pc.createOffer()
          .then((offer) => pc.setLocalDescription(offer)
            .then(() => this._sendSignal(peerId, { type: 'offer', sdp: pc.localDescription })));
      }
      return this._peers[peerId];
    },

    // Drive a client-side ICE restart for a peer whose connection failed or
    // dropped, instead of waiting on the server reap. Schedules one attempt;
    // the oniceconnectionstatechange handler re-schedules the next one if the
    // restart does not recover the connection.
    _scheduleIceRestart(peerId) {
      const peer = this._peers[peerId];
      if (!peer) return;
      // Only the lower-id side initiates; the other side waits for the incoming
      // restart offer and answers it through the existing onCallSignal path.
      if (!window.chatCallShouldDriveIceRestart(this.currentUserId, peerId)) return;
      if (peer.iceRestartTimer) return;                        // one pending attempt at a time
      if (peer.iceRestartAttempts >= MAX_ICE_RESTARTS) return; // gave up; server reap takes over
      const delay = window.chatCallIceRestartDelay(peer.pc.iceConnectionState, peer.iceRestartAttempts);
      peer.iceRestartTimer = setTimeout(() => {
        peer.iceRestartTimer = null;
        this._performIceRestart(peerId);
      }, delay);
    },

    async _performIceRestart(peerId) {
      const peer = this._peers[peerId];
      if (!peer) return;
      const pc = peer.pc;
      const state = pc.iceConnectionState;
      // Recovered or torn down while the timer was pending: nothing to do.
      if (state === 'connected' || state === 'completed' || state === 'closed') return;
      peer.iceRestartAttempts++;
      try {
        const offer = await pc.createOffer({ iceRestart: true });
        await pc.setLocalDescription(offer);
        this._sendSignal(peerId, { type: 'offer', sdp: pc.localDescription });
      } catch (e) {
        console.warn('ICE restart failed:', e);
      }
    },

    _closePeer(peerId) {
      const peer = this._peers[peerId];
      if (!peer) return;
      if (peer.iceRestartTimer) { clearTimeout(peer.iceRestartTimer); peer.iceRestartTimer = null; }
      try { peer.pc.close(); } catch (e) { /* ignore */ }
      if (peer.audioEl && peer.audioEl.parentNode) peer.audioEl.parentNode.removeChild(peer.audioEl);
      const next = Object.assign({}, this.remoteStreams);
      delete next[peerId];
      this.remoteStreams = next;
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
