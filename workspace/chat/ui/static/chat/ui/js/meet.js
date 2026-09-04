// The public meeting page. One component, four phases: name -> lobby -> room
// -> over. Composes chatCallMixin through its transport seam so the WebRTC
// and presence code is the member room's, not a copy.
//
// The guest token never travels in a URL: it is sent as the X-Meeting-Token
// header, which is why the event stream below is a fetch() reader rather
// than an EventSource (which cannot set headers).

/**
 * Split a raw SSE buffer into complete frames plus the trailing partial one.
 * Pure: the reader owns the buffer, this owns the wire format.
 * @param {string} buffer
 * @returns {{frames: Array<{id: ?string, payload: object}>, rest: string}}
 */
function chatMeetParseSseChunk(buffer) {
  const frames = [];
  let rest = buffer;
  let sep = rest.indexOf('\n\n');
  while (sep !== -1) {
    const raw = rest.slice(0, sep);
    rest = rest.slice(sep + 2);
    sep = rest.indexOf('\n\n');
    if (raw.startsWith(':')) continue;
    let id = null;
    let data = '';
    for (const line of raw.split('\n')) {
      if (line.startsWith('id:')) id = line.slice(3).trim();
      else if (line.startsWith('data:')) data += line.slice(5).trim();
    }
    if (!data) continue;
    try {
      frames.push({ id, payload: JSON.parse(data) });
    } catch (e) { /* malformed frame: skip it rather than kill the reader */ }
  }
  return { frames, rest };
}

window.chatMeetSseMixin = function chatMeetSseMixin() {
  return {
    _streamAbort: null,
    _streamSawFrame: false,
    _lastEventId: null,
    _streamBackoffMs: 1000,

    _openStream() {
      if (this._streamAbort) return;
      const controller = new AbortController();
      this._streamAbort = controller;
      const headers = { 'X-Meeting-Token': this.token };
      if (this._lastEventId) headers['Last-Event-ID'] = this._lastEventId;
      fetch(`/api/v1/chat/meet/${this.slug}/stream`, { headers, signal: controller.signal })
        .then(async (resp) => {
          if (!resp.ok || !resp.body) throw new Error(`stream ${resp.status}`);
          this._streamBackoffMs = 1000;
          const reader = resp.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const { frames, rest } = chatMeetParseSseChunk(buffer);
            buffer = rest;
            for (const frame of frames) {
              this._streamSawFrame = true;
              if (frame.id) this._lastEventId = frame.id;
              await this._dispatchStreamEvent(frame.payload);
            }
          }
        })
        .catch(() => { /* closed or failed: the reconnect below decides */ })
        .finally(() => {
          this._streamAbort = null;
          if (this.phase !== 'lobby' && this.phase !== 'room') return;
          // A first stream that closes without a single frame is the server
          // saying "nothing for you": the guest was refused, removed or the
          // meeting ended before this connection. Ask /state once instead of
          // reconnecting into the same empty answer forever.
          if (!this._streamSawFrame) {
            this.resume();
            return;
          }
          setTimeout(() => this._openStream(), this._streamBackoffMs);
          this._streamBackoffMs = Math.min(this._streamBackoffMs * 2, 30000);
        });
    },

    _closeStream() {
      if (this._streamAbort) {
        this._streamAbort.abort();
        this._streamAbort = null;
      }
    },

    async _dispatchStreamEvent(payload) {
      const name = payload.event || '';
      if (name.startsWith('call_')) {
        window.dispatchEvent(new CustomEvent('chat-' + name, { detail: payload.data }));
      } else if (name === 'message') {
        this.onIncomingMessage(payload.data && payload.data.message);
      } else if (name.startsWith('meeting_')) {
        await this.onMeetingEvent(payload);
      }
    },
  };
};

window.chatMeetMessagesMixin = function chatMeetMessagesMixin() {
  return {
    messages: [],
    draft: '',
    sending: false,

    async loadMessages() {
      const resp = await fetch(`/api/v1/chat/meet/${this.slug}/messages`, {
        headers: this._callHeaders(),
      });
      if (!resp.ok) return;
      const data = await resp.json();
      this.messages = data.messages || [];
      this._scrollMessages();
    },

    onIncomingMessage(message) {
      if (!message || this.messages.some((m) => m.uuid === message.uuid)) return;
      this.messages.push(message);
      this._scrollMessages();
    },

    async sendMessage() {
      const body = this.draft.trim();
      if (!body || this.sending) return;
      this.sending = true;
      try {
        const resp = await fetch(`/api/v1/chat/meet/${this.slug}/messages`, {
          method: 'POST',
          headers: this._callHeaders({ json: true }),
          body: JSON.stringify({ body }),
        });
        if (resp.ok) {
          this.onIncomingMessage(await resp.json());
          this.draft = '';
        } else {
          this.error = 'Your message was not sent.';
        }
      } finally {
        this.sending = false;
      }
    },

    // A guest message carries no id of its own author beyond the name the
    // guest typed, so "mine" is that name matching. Two guests picking the
    // same name only mis-align a bubble; nothing else reads this.
    isOwnMessage(message) {
      const author = message && message.author;
      return !!(author && author.is_guest && author.display_name === this.displayName);
    },

    formatTime(iso) {
      const d = new Date(iso);
      return isNaN(d) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    },

    _scrollMessages() {
      const el = this.$refs && this.$refs.messageList;
      if (el) queueMicrotask(() => { el.scrollTop = el.scrollHeight; });
    },
  };
};

function chatMeetApp(slug) {
  return {
    ...chatCallMixin(),
    ...chatMeetSseMixin(),
    ...chatMeetMessagesMixin(),

    slug,
    phase: 'name',
    summary: null,
    displayName: '',
    token: null,
    knocking: false,
    error: '',
    overReason: null,
    callRole: 'room',
    // The call mixin gates several of its methods on "is there a conversation
    // to talk to". A guest never learns one, and never needs to: the
    // _callEndpoint override below ignores the id it is handed, so this stub
    // is only the flag those guards read.
    activeConversation: { uuid: slug },
    speakingIds: {},
    pinnedKey: null,
    pinnedManually: false,
    callElapsed: '00:00',
    _callStartMs: null,
    _durationTimer: null,
    _callWatchTimer: null,
    // After the spreads: chatCallMixin declares its own null default.
    currentParticipantKey: null,

    async init() {
      this._initCallSounds?.();
      await this.loadSummary();
      const stored = sessionStorage.getItem(`meet:${slug}`);
      if (stored) {
        try {
          const saved = JSON.parse(stored);
          this.token = saved.token;
          this.displayName = saved.displayName;
          this.currentParticipantKey = saved.participantKey;
          await this.resume();
        } catch (e) { this.reset(); }
      }
      window.addEventListener('pagehide', () => { if (this.inCall) this._leaveBeacon(); });
    },

    // -- Transport seam --------------------------------------
    // Every chatCallMixin request goes to the meet endpoints with the token,
    // and the "state" read replaces the conversation's /call.
    _callEndpoint(action) {
      return `/api/v1/chat/meet/${this.slug}/${action || 'state'}`;
    },
    _callHeaders({ json = false } = {}) {
      const headers = { 'X-Meeting-Token': this.token || '' };
      if (json) headers['Content-Type'] = 'application/json';
      return headers;
    },
    // A guest reaped by the stale sweep (12s without a heartbeat) gets 400
    // on the next heartbeat instead of silently re-arming presence: re-join.
    async _sendHeartbeat() {
      if (!this.inCall) return;
      let resp;
      try {
        resp = await fetch(this._callEndpoint('heartbeat'), {
          method: 'POST',
          headers: this._callHeaders({ json: true }),
          body: JSON.stringify({ media_state: this._mediaState() }),
        });
      } catch (e) { return; }
      if (resp.status === 400 || resp.status === 404) {
        this.inCall = false;
        await this.resume();
      }
    },
    async _refreshCallState() {
      const resp = await fetch(this._callEndpoint(''), { headers: this._callHeaders() });
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.participant_key) this.currentParticipantKey = data.participant_key;
      if (data.ice_servers) this._iceServers = data.ice_servers;
      this.callSession = data.active ? data : null;
      this.callParticipants = data.active ? (data.participants || []) : [];
    },
    // The member room re-advertises a joinable call in a banner; this page
    // has none, and leaveCall's tail call would otherwise re-read the state
    // of a call the guest has just left.
    _syncCallBanner() {},
    // The base beacon is keyed on the call's conversation id, which a guest's
    // state never carries. Same POST, addressed the guest way.
    _leaveBeacon() {
      if (!this.token) return;
      try {
        fetch(this._callEndpoint('leave'), {
          method: 'POST',
          headers: this._callHeaders(),
          keepalive: true,
        });
      } catch (e) { /* the page is going away */ }
    },

    // -- The meeting itself ----------------------------------
    async loadSummary() {
      const resp = await fetch(`/api/v1/chat/meet/${this.slug}`);
      this.summary = resp.ok ? await resp.json() : null;
    },
    summaryLine() {
      if (!this.summary || !this.summary.start) return '';
      const d = new Date(this.summary.start);
      return isNaN(d) ? '' : d.toLocaleString();
    },
    isFull() {
      return !!(this.summary && this.summary.max_participants
        && this.summary.participant_count >= this.summary.max_participants);
    },
    capacityLabel() {
      const max = this.callSession && this.callSession.max_participants;
      return max ? `${this.callParticipants.length} / ${max}` : String(this.callParticipants.length);
    },

    async knock() {
      const name = this.displayName.trim();
      if (this.knocking || !name) return;
      this.knocking = true;
      this.error = '';
      try {
        const resp = await fetch(`/api/v1/chat/meet/${this.slug}/knock`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ display_name: name }),
        });
        if (resp.status === 423) { this.error = 'This meeting is locked.'; return; }
        if (resp.status === 429) { this.error = 'Too many attempts. Please wait a moment.'; return; }
        if (!resp.ok) { this.error = 'This meeting cannot be joined right now.'; return; }
        const data = await resp.json();
        this.token = data.token;
        this.displayName = data.display_name || name;
        this.currentParticipantKey = data.participant_key;
        sessionStorage.setItem(`meet:${this.slug}`, JSON.stringify({
          token: this.token,
          displayName: this.displayName,
          participantKey: this.currentParticipantKey,
        }));
        this.phase = 'lobby';
        this._openStream();
      } catch (e) {
        this.error = 'This meeting cannot be joined right now.';
      } finally {
        this.knocking = false;
      }
    },

    async resume() {
      let resp;
      try {
        resp = await fetch(this._callEndpoint(''), { headers: this._callHeaders() });
      } catch (e) { return; }
      if (resp.status === 404) { this.reset(); return; }
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.admitted) {
        this.phase = 'room';
        this._openStream();
        await this.loadMessages();
        await this.joinWhenCallStarts();
      } else if (data.state === 'waiting') {
        this.phase = 'lobby';
        this._openStream();
      } else {
        this.finish(data.state === 'refused' || data.state === 'removed' ? data.state : 'ended');
      }
    },

    // A guest admitted before anyone starts the call cannot be told when one
    // starts: call_started only fans out to the session's own participants,
    // and this guest is not one yet. So the room phase re-reads its own state
    // until there is a call to join, then joins it once.
    async joinWhenCallStarts() {
      if (this.inCall || this.joiningCall) return;
      await this._refreshCallState();
      if (this.callSession) {
        this._disarmCallWatch();
        await this.startOrJoinCall();
        if (this.inCall) this._startDurationTimer();
        return;
      }
      this._armCallWatch();
    },

    _armCallWatch() {
      if (this._callWatchTimer) return;
      this._callWatchTimer = setInterval(() => {
        if (this.phase !== 'room' || this.inCall) { this._disarmCallWatch(); return; }
        this.joinWhenCallStarts();
      }, 8000);
    },

    _disarmCallWatch() {
      if (this._callWatchTimer) { clearInterval(this._callWatchTimer); this._callWatchTimer = null; }
    },

    async onMeetingEvent(payload) {
      switch (payload.event) {
        case 'meeting_admitted':
          this.phase = 'room';
          await this.loadMessages();
          await this.joinWhenCallStarts();
          break;
        case 'meeting_refused': this.finish('refused'); break;
        case 'meeting_removed': await this.leaveCall(); this.finish('removed'); break;
        case 'meeting_ended': await this.leaveCall(); this.finish('ended'); break;
        default: break;
      }
    },

    onCallEnded() {
      return this.leaveCall().finally(() => this.finish('ended'));
    },

    // Copied from the member room: a departing peer that held the manual pin
    // releases it, so the stage falls back to the automatic spotlight.
    onCallParticipantLeft(detail) {
      if (this.inCall && !window.chatCallEventForCurrentSession(detail, this.callSession)) return;
      if (detail.participant_key !== this.currentParticipantKey) this._playCallCue('peer-leave');
      this.callParticipants = this.callParticipants.filter((p) => p.participant_key !== detail.participant_key);
      this._closePeer(detail.participant_key);
      if (this.pinnedKey === detail.participant_key) {
        this.pinnedKey = null;
        this.pinnedManually = false;
      }
    },

    leaveLobby() {
      this._closeStream();
      this.reset();
    },

    leaveRoom() {
      return this.leaveCall().finally(() => {
        this._leaveBeacon();
        this.finish('left');
      });
    },

    finish(reason) {
      this._closeStream();
      this._disarmCallWatch();
      this._stopDurationTimer();
      this.overReason = reason;
      this.phase = 'over';
    },

    overTitle() {
      return {
        refused: 'The host did not let you in',
        removed: 'You were removed from the meeting',
        ended: 'The meeting has ended',
        left: 'You left the meeting',
      }[this.overReason] || 'Meeting closed';
    },
    overDetail() {
      return (this.overReason === 'refused' || this.overReason === 'removed')
        ? 'You can close this tab.'
        : 'Thanks for joining.';
    },
    canReturn() {
      return this.overReason !== 'refused' && this.overReason !== 'removed';
    },

    reset() {
      this._closeStream();
      this._disarmCallWatch();
      this._stopDurationTimer();
      sessionStorage.removeItem(`meet:${this.slug}`);
      this.token = null;
      this.currentParticipantKey = null;
      this.overReason = null;
      this.error = '';
      this.messages = [];
      this.phase = 'name';
    },

    // -- Call duration ---------------------------------------
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

    // -- Stage helpers the shared partial reads --------------
    // Same names, same bodies as the member room's (room.js), so both pages
    // compute their tiles through the same call_room.js helpers.
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
  };
}

window.chatMeetParseSseChunk = chatMeetParseSseChunk;
window.chatMeetApp = chatMeetApp;
