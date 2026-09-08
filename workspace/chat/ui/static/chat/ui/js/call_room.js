// Voice room: pure helpers shared by the room page, the main-tab observer and
// the public guest page. The Alpine room factory lives in room.js; speaking-meter
// wiring that touches AudioContext is validated in a real browser, not here.

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

function chatCallRoomUrl(conversationId) {
  return `/chat/room/${conversationId}`;
}

function chatCallRoomTabName(conversationId) {
  // Deterministic tab name: window.open with this name reactivates the
  // existing room tab instead of opening a duplicate.
  return `chat-room-${conversationId}`;
}

function chatCallBannerAction(callActive, participants, selfKey) {
  // What the main (observer) tab should offer for an ongoing call:
  //   null     -> no active call, hide the banner
  //   'return' -> I am a participant, reactivate my room tab
  //   'join'   -> a call is running but I am not in it
  if (!callActive) return null;
  const inIt = (participants || []).some((p) => p.participant_key === selfKey);
  return inIt ? 'return' : 'join';
}

function chatIsSpeaking(level, threshold) {
  const t = (typeof threshold === 'number') ? threshold : 0.05;
  return typeof level === 'number' && level >= t;
}

function chatCallShouldOwnMedia(role) {
  // Only the observer role gives up the microphone / peer connections.
  return role !== 'observer';
}

function chatCallAutoPinTarget(participants, pinnedManually) {
  // The automatic spotlight pick: the first participant actively sharing their
  // screen. A manual pin always wins, so we yield when one is set. Derived from
  // the live participants list, so it reflects who is sharing *now*.
  if (pinnedManually) return null;
  const sharer = (participants || []).find(
    (p) => p && p.media_state && p.media_state.screen === true,
  );
  return sharer ? sharer.participant_key : null;
}

function chatCallSpotlightTarget(participants, pinnedKey, pinnedManually) {
  // Which participant to show large. A manual pin wins while that participant is
  // still in the call; otherwise the spotlight is derived from live state - the
  // active screen sharer, or the equal grid (null). Deriving instead of latching
  // a one-off event means a sharer is spotlighted even for someone who joined
  // after the share began, and the spotlight clears the moment sharing stops.
  const list = participants || [];
  if (pinnedManually && pinnedKey != null) {
    return list.some((p) => p.participant_key === pinnedKey) ? pinnedKey : null;
  }
  return chatCallAutoPinTarget(list, pinnedManually);
}

// The call stage's shared state and helpers: the tile split, the spotlight,
// the manual pin and the elapsed clock. The member room, the guest page and
// the meeting page all mount call_stage.html, which binds every name below -
// held here so the three cannot compute their tiles differently.
window.chatCallStageMixin = function chatCallStageMixin() {
  return {
    speakingIds: {},
    pinnedKey: null,
    pinnedManually: false,
    callElapsed: '00:00',
    _callStartMs: null,
    _durationTimer: null,

    // The status bar's "2 / 6", or a bare count when the call names no cap.
    capacityLabel() {
      const max = this.callSession && this.callSession.max_participants;
      const n = (this.callParticipants || []).length;
      return max ? `${n} / ${max}` : String(n);
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

    // Click a tile to spotlight it; click the pinned tile again to return to
    // the grid. Any click marks the choice manual so auto-pin yields to it.
    pinTile(participantKey) {
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

    // Everyone except the spotlighted participant, for the thumbnail strip.
    stripParticipants() {
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

    // Overrides the call mixin's: a departing peer that held the manual pin
    // releases it, so the stage falls back to the automatic spotlight.
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

    // Idempotent. Prefers the server-supplied start so every participant
    // reads the same clock rather than counting from their own join.
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
  };
};

window.chatRoomFormatDuration = chatRoomFormatDuration;
window.chatCallRoomUrl = chatCallRoomUrl;
window.chatCallRoomTabName = chatCallRoomTabName;
window.chatCallBannerAction = chatCallBannerAction;
window.chatIsSpeaking = chatIsSpeaking;
window.chatCallShouldOwnMedia = chatCallShouldOwnMedia;
window.chatCallSpotlightTarget = chatCallSpotlightTarget;
window.chatCallAutoPinTarget = chatCallAutoPinTarget;
