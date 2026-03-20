/**
 * CallManager — WebRTC P2P mesh transport abstraction.
 *
 * Pure JS, zero Alpine dependency. Communicates outward via CustomEvents on
 * `window` and listens to SSE signals dispatched by the core SSE client.
 *
 * Events emitted:
 *   call:state-changed        { state, conversationId, callId }
 *   call:participants-changed  { participants }
 *   call:quality-warning       { userId, rtt, packetLoss }
 *   call:error                 { message }
 *   call:ended                 { callId, duration, participantCount }
 */
(function () {
  'use strict';

  // ── Constants ──────────────────────────────────────────
  var QUALITY_INTERVAL = 5000;
  var BITRATE_HIGH = 48000;
  var BITRATE_MEDIUM = 24000;
  var BITRATE_LOW = 16000;
  var PACKET_LOSS_THRESHOLD_HIGH = 0.05;
  var PACKET_LOSS_THRESHOLD_CRITICAL = 0.15;

  // ── Helpers ────────────────────────────────────────────
  function csrf() {
    var el = document.querySelector('[name=csrfmiddlewaretoken]');
    if (el) return el.value;
    var match = document.cookie.split('; ').find(function (c) { return c.startsWith('csrftoken='); });
    return match ? match.split('=')[1] : '';
  }

  function emit(name, detail) {
    window.dispatchEvent(new CustomEvent(name, { detail: detail }));
  }

  function apiPost(url, body) {
    var opts = {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRFToken': csrf(), 'Content-Type': 'application/json' },
    };
    if (body !== undefined) {
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts);
  }

  function apiPostKeepalive(url, body) {
    var opts = {
      method: 'POST',
      credentials: 'same-origin',
      keepalive: true,
      headers: { 'X-CSRFToken': csrf(), 'Content-Type': 'application/json' },
    };
    if (body !== undefined) {
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts);
  }

  // ── CallManager ────────────────────────────────────────
  function CallManager(currentUserId) {
    this.currentUserId = currentUserId;
    this.state = 'idle';            // idle | ringing | connecting | active
    this.conversationId = null;
    this.callId = null;
    this.muted = false;
    this.localStream = null;
    this.iceServers = [];
    this.peers = {};                // userId -> RTCPeerConnection
    this.remoteStreams = {};         // userId -> MediaStream
    this.participants = [];         // [{ id, name }]
    this._qualityTimer = null;

    this._bindSSE();
  }

  // ── State management ───────────────────────────────────
  CallManager.prototype._setState = function (state) {
    this.state = state;
    emit('call:state-changed', {
      state: this.state,
      conversationId: this.conversationId,
      callId: this.callId,
    });
  };

  CallManager.prototype._emitParticipants = function () {
    emit('call:participants-changed', { participants: this.participants.slice() });
  };

  // ── Microphone ─────────────────────────────────────────
  CallManager.prototype._acquireMic = function () {
    var self = this;
    return navigator.mediaDevices.getUserMedia({ audio: true, video: false })
      .then(function (stream) {
        self.localStream = stream;
        return stream;
      })
      .catch(function (err) {
        emit('call:error', { message: 'Microphone access denied: ' + err.message });
        throw err;
      });
  };

  CallManager.prototype._releaseMic = function () {
    if (this.localStream) {
      this.localStream.getTracks().forEach(function (t) { t.stop(); });
      this.localStream = null;
    }
  };

  // ── Public API ─────────────────────────────────────────

  /**
   * Start a new call in the given conversation.
   */
  CallManager.prototype.startCall = function (conversationId) {
    var self = this;
    if (this.state !== 'idle') {
      emit('call:error', { message: 'Already in a call' });
      return Promise.reject(new Error('Already in a call'));
    }

    this.conversationId = conversationId;
    this._setState('connecting');

    return this._acquireMic()
      .then(function () {
        return apiPost('/api/v1/chat/conversations/' + conversationId + '/call/start');
      })
      .then(function (resp) {
        if (!resp.ok) {
          return resp.json().then(function (d) { throw new Error(d.error || 'Failed to start call'); });
        }
        return resp.json();
      })
      .then(function (data) {
        self.callId = data.call_id;
        self.iceServers = data.ice_servers || [];
        self.participants = [{ id: self.currentUserId, name: '' }];
        self._setState('ringing');
        self._startQualityMonitor();
        self._emitParticipants();
      })
      .catch(function (err) {
        self._cleanup();
        emit('call:error', { message: err.message });
        throw err;
      });
  };

  /**
   * Join an existing call in the given conversation.
   */
  CallManager.prototype.joinCall = function (conversationId) {
    var self = this;
    if (this.state !== 'idle') {
      emit('call:error', { message: 'Already in a call' });
      return Promise.reject(new Error('Already in a call'));
    }

    this.conversationId = conversationId;
    this._setState('connecting');

    return this._acquireMic()
      .then(function () {
        return apiPost('/api/v1/chat/conversations/' + conversationId + '/call/join');
      })
      .then(function (resp) {
        if (!resp.ok) {
          return resp.json().then(function (d) { throw new Error(d.error || 'Failed to join call'); });
        }
        return resp.json();
      })
      .then(function (data) {
        self.callId = data.call_id;
        self.iceServers = data.ice_servers || [];
        self.participants = data.participants || [];
        self._setState('active');
        self._startQualityMonitor();
        self._emitParticipants();

        // Create peer connections to every existing participant.
        // Convention: newer joiner (us) sends the offer.
        self.participants.forEach(function (p) {
          if (p.id !== self.currentUserId) {
            self._createPeerConnection(p.id, true);
          }
        });
      })
      .catch(function (err) {
        self._cleanup();
        emit('call:error', { message: err.message });
        throw err;
      });
  };

  /**
   * Leave the current call. Uses keepalive fetch for reliability on unload.
   */
  CallManager.prototype.leaveCall = function () {
    if (!this.conversationId) return Promise.resolve();

    var conversationId = this.conversationId;
    this._cleanup();

    return apiPostKeepalive(
      '/api/v1/chat/conversations/' + conversationId + '/call/leave'
    ).catch(function () { /* best effort */ });
  };

  /**
   * Reject an incoming call.
   */
  CallManager.prototype.rejectCall = function (conversationId) {
    var cid = conversationId || this.conversationId;
    this._cleanup();

    return apiPost('/api/v1/chat/conversations/' + cid + '/call/reject')
      .catch(function () { /* best effort */ });
  };

  /**
   * Toggle mute. Returns the new muted state.
   */
  CallManager.prototype.toggleMute = function () {
    if (!this.localStream) return Promise.resolve(this.muted);

    this.muted = !this.muted;
    var self = this;

    this.localStream.getAudioTracks().forEach(function (track) {
      track.enabled = !self.muted;
    });

    if (this.conversationId) {
      apiPost(
        '/api/v1/chat/conversations/' + this.conversationId + '/call/mute',
        { muted: this.muted }
      ).catch(function () { /* best effort */ });
    }

    return Promise.resolve(this.muted);
  };

  // ── SSE event binding ──────────────────────────────────
  CallManager.prototype._bindSSE = function () {
    var self = this;

    window.addEventListener('sse:chat.call.signal', function (e) {
      self.handleSignal(e.detail);
    });
    window.addEventListener('sse:chat.call.participant_joined', function (e) {
      self.onParticipantJoined(e.detail);
    });
    window.addEventListener('sse:chat.call.participant_left', function (e) {
      self.onParticipantLeft(e.detail);
    });
    window.addEventListener('sse:chat.call.ended', function (e) {
      self._onCallEnded(e.detail);
    });
    window.addEventListener('sse:chat.call.mute_changed', function (e) {
      self._onMuteChanged(e.detail);
    });

    // Clean up on page unload
    window.addEventListener('beforeunload', function () {
      if (self.state !== 'idle') {
        self.leaveCall();
      }
    });
  };

  // ── Signal handling (SDP offer / answer / ICE) ─────────
  CallManager.prototype.handleSignal = function (detail) {
    if (!this.callId || detail.call_id !== this.callId) return;

    var fromUser = detail.from_user;
    var type = detail.type;
    var payload = detail.payload;
    var self = this;

    if (type === 'offer') {
      // Received an offer — create a peer connection if needed, then answer.
      var pc = self.peers[fromUser] || self._createPeerConnection(fromUser, false);
      pc.setRemoteDescription(new RTCSessionDescription(payload))
        .then(function () { return pc.createAnswer(); })
        .then(function (answer) { return pc.setLocalDescription(answer); })
        .then(function () {
          self._sendSignal(fromUser, 'answer', pc.localDescription.toJSON());
        })
        .catch(function (err) {
          emit('call:error', { message: 'Signal error (offer): ' + err.message });
        });

    } else if (type === 'answer') {
      var peerConn = self.peers[fromUser];
      if (peerConn) {
        peerConn.setRemoteDescription(new RTCSessionDescription(payload))
          .catch(function (err) {
            emit('call:error', { message: 'Signal error (answer): ' + err.message });
          });
      }

    } else if (type === 'ice') {
      var peer = self.peers[fromUser];
      if (peer && payload) {
        peer.addIceCandidate(new RTCIceCandidate(payload))
          .catch(function () { /* non-fatal */ });
      }
    }
  };

  // ── Participant lifecycle ──────────────────────────────
  CallManager.prototype.onParticipantJoined = function (detail) {
    if (!this.callId || detail.call_id !== this.callId) return;

    var userId = detail.user_id;
    var userName = detail.user_name || '';

    // Avoid duplicates
    var exists = this.participants.some(function (p) { return p.id === userId; });
    if (!exists) {
      this.participants.push({ id: userId, name: userName });
    }

    if (this.state === 'ringing') {
      this._setState('active');
    }

    this._emitParticipants();

    // The existing participant waits for the joiner to send the offer.
    // No need to create a peer connection here — the joiner initiates.
  };

  CallManager.prototype.onParticipantLeft = function (detail) {
    if (!this.callId || detail.call_id !== this.callId) return;

    var userId = detail.user_id;

    this.participants = this.participants.filter(function (p) { return p.id !== userId; });
    this._closePeer(userId);
    this._emitParticipants();
  };

  CallManager.prototype._onCallEnded = function (detail) {
    var callId = this.callId;
    this._cleanup();

    emit('call:ended', {
      callId: detail.call_id || callId,
      duration: detail.duration || '',
      participantCount: detail.participant_count || 0,
    });
  };

  CallManager.prototype._onMuteChanged = function (detail) {
    if (!this.callId || detail.call_id !== this.callId) return;
    // Update the participants list so UI can reflect mute state
    emit('call:participants-changed', {
      participants: this.participants.slice(),
      mutedUserId: detail.user_id,
      muted: detail.muted,
    });
  };

  // ── Peer connection management ─────────────────────────
  CallManager.prototype._createPeerConnection = function (userId, isOfferer) {
    var self = this;
    var config = { iceServers: this.iceServers };
    var pc = new RTCPeerConnection(config);

    this.peers[userId] = pc;

    // Add local audio tracks
    if (this.localStream) {
      this.localStream.getTracks().forEach(function (track) {
        pc.addTrack(track, self.localStream);
      });
    }

    // Handle incoming remote tracks
    pc.ontrack = function (event) {
      var stream = event.streams && event.streams[0];
      if (!stream) {
        stream = new MediaStream();
        stream.addTrack(event.track);
      }
      self.remoteStreams[userId] = stream;

      // Create or reuse a hidden <audio> element for playback
      var audioId = 'call-audio-' + userId;
      var audio = document.getElementById(audioId);
      if (!audio) {
        audio = document.createElement('audio');
        audio.id = audioId;
        audio.autoplay = true;
        audio.style.display = 'none';
        document.body.appendChild(audio);
      }
      audio.srcObject = stream;
    };

    // ICE candidate relay
    pc.onicecandidate = function (event) {
      if (event.candidate) {
        self._sendSignal(userId, 'ice', event.candidate.toJSON());
      }
    };

    // Connection state monitoring
    pc.oniceconnectionstatechange = function () {
      var state = pc.iceConnectionState;
      if (state === 'failed' || state === 'disconnected') {
        emit('call:quality-warning', { userId: userId, rtt: -1, packetLoss: -1 });
      }
      if (state === 'failed') {
        self._closePeer(userId);
      }
    };

    // If we are the offerer (newer joiner), initiate the SDP exchange
    if (isOfferer) {
      pc.createOffer()
        .then(function (offer) { return pc.setLocalDescription(offer); })
        .then(function () {
          self._sendSignal(userId, 'offer', pc.localDescription.toJSON());
        })
        .catch(function (err) {
          emit('call:error', { message: 'Offer error: ' + err.message });
        });
    }

    return pc;
  };

  CallManager.prototype._sendSignal = function (toUserId, type, payload) {
    if (!this.conversationId) return;

    apiPost(
      '/api/v1/chat/conversations/' + this.conversationId + '/call/signal',
      { to_user: toUserId, type: type, payload: payload }
    ).catch(function (err) {
      emit('call:error', { message: 'Signaling failed: ' + err.message });
    });
  };

  CallManager.prototype._closePeer = function (userId) {
    var pc = this.peers[userId];
    if (pc) {
      pc.ontrack = null;
      pc.onicecandidate = null;
      pc.oniceconnectionstatechange = null;
      pc.close();
      delete this.peers[userId];
    }
    delete this.remoteStreams[userId];

    // Remove the audio element
    var audio = document.getElementById('call-audio-' + userId);
    if (audio) {
      audio.srcObject = null;
      audio.remove();
    }
  };

  // ── Quality monitoring ─────────────────────────────────
  CallManager.prototype._startQualityMonitor = function () {
    var self = this;
    this._stopQualityMonitor();
    this._qualityTimer = setInterval(function () {
      self._monitorQuality();
    }, QUALITY_INTERVAL);
  };

  CallManager.prototype._stopQualityMonitor = function () {
    if (this._qualityTimer) {
      clearInterval(this._qualityTimer);
      this._qualityTimer = null;
    }
  };

  CallManager.prototype._monitorQuality = function () {
    var self = this;
    var userIds = Object.keys(this.peers);

    userIds.forEach(function (userId) {
      var pc = self.peers[userId];
      if (!pc || pc.connectionState === 'closed') return;

      pc.getStats().then(function (stats) {
        var rtt = 0;
        var packetLoss = 0;
        var hasData = false;

        stats.forEach(function (report) {
          if (report.type === 'candidate-pair' && report.state === 'succeeded') {
            rtt = report.currentRoundTripTime || 0;
            hasData = true;
          }
          if (report.type === 'inbound-rtp' && report.kind === 'audio') {
            var lost = report.packetsLost || 0;
            var received = report.packetsReceived || 0;
            var total = lost + received;
            packetLoss = total > 0 ? lost / total : 0;
            hasData = true;
          }
        });

        if (!hasData) return;

        // Adaptive bitrate
        var targetBitrate = BITRATE_HIGH;
        if (packetLoss > PACKET_LOSS_THRESHOLD_CRITICAL) {
          targetBitrate = BITRATE_LOW;
        } else if (packetLoss > PACKET_LOSS_THRESHOLD_HIGH) {
          targetBitrate = BITRATE_MEDIUM;
        }

        // Apply bitrate limit via sender parameters
        var senders = pc.getSenders();
        senders.forEach(function (sender) {
          if (!sender.track || sender.track.kind !== 'audio') return;
          var params = sender.getParameters();
          if (!params.encodings || params.encodings.length === 0) {
            params.encodings = [{}];
          }
          params.encodings[0].maxBitrate = targetBitrate;
          sender.setParameters(params).catch(function () { /* best effort */ });
        });

        // Emit warning when quality degrades
        if (packetLoss > PACKET_LOSS_THRESHOLD_HIGH || rtt > 0.3) {
          emit('call:quality-warning', {
            userId: Number(userId),
            rtt: Math.round(rtt * 1000),
            packetLoss: Math.round(packetLoss * 100),
          });
        }
      }).catch(function () { /* stats unavailable */ });
    });
  };

  // ── Cleanup ────────────────────────────────────────────
  CallManager.prototype._cleanup = function () {
    this._stopQualityMonitor();

    var self = this;
    Object.keys(this.peers).forEach(function (userId) {
      self._closePeer(userId);
    });

    this._releaseMic();
    this.peers = {};
    this.remoteStreams = {};
    this.participants = [];
    this.conversationId = null;
    this.callId = null;
    this.muted = false;
    this._setState('idle');
  };

  // ── Export ─────────────────────────────────────────────
  window.CallManager = CallManager;
})();
