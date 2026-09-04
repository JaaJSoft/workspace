// Host controls for a meeting room: the lobby (waiting guests), lock, remove
// and end. Spread into chatRoomApp; inert when the room has no meeting.
window.chatMeetingHostMixin = function chatMeetingHostMixin() {
  return {
    meeting: null,
    lobby: [],
    lobbyOpen: false,
    _lobbyRefreshTimer: null,
    _onSseReconnect: null,

    _hostHeaders() {
      return { 'Content-Type': 'application/json', 'X-CSRFToken': this._csrf() };
    },
    async _hostPost(path, body, { on409 } = {}) {
      const resp = await fetch(`/api/v1/chat/meetings/${this.meeting.uuid}${path}`, {
        method: 'POST', headers: this._hostHeaders(), body: body ? JSON.stringify(body) : undefined,
      });
      if (!resp.ok) {
        if (resp.status === 409 && on409) {
          window.AppAlert?.warning(on409);
        } else {
          window.AppAlert?.error('That did not work. Please try again.');
        }
        return null;
      }
      return resp.json();
    },

    async loadLobby() {
      if (!this.meeting) return;
      const resp = await fetch(`/api/v1/chat/meetings/${this.meeting.uuid}/lobby`);
      this.lobby = resp.ok ? await resp.json() : [];
    },

    // meeting_guest_waiting is delivered through the host's own u:<id>
    // mailbox, and a mailbox drain is destructive while every one of that
    // host's tabs polls it - there is no leader election. So the tab showing
    // this room is not the one that receives the knock roughly half the time,
    // and a guest can sit in the lobby unannounced.
    //
    // FOLLOW-UP: the real fix is fanning meeting_guest_waiting out per tab (or
    // per room) rather than per user, so the room that can act on it is the
    // one that gets it. Until then the panel re-reads on a schedule, and
    // whenever the global stream reconnects - the moment a drain was most
    // likely missed.
    _startLobbyRefresh() {
      if (!this.meeting || this._lobbyRefreshTimer) return;
      this._lobbyRefreshTimer = setInterval(() => this.loadLobby(), 30000);
      this._onSseReconnect = () => this.loadLobby();
      window.addEventListener('sse:reconnect', this._onSseReconnect);
    },

    _stopLobbyRefresh() {
      if (this._lobbyRefreshTimer) {
        clearInterval(this._lobbyRefreshTimer);
        this._lobbyRefreshTimer = null;
      }
      if (this._onSseReconnect) {
        window.removeEventListener('sse:reconnect', this._onSseReconnect);
        this._onSseReconnect = null;
      }
    },
    async onGuestWaiting(detail) {
      if (!this.meeting || !detail || detail.meeting_id !== this.meeting.uuid) return;
      this._playCallCue?.('peer-join');
      await this.loadLobby();
    },
    async admitGuest(uuid) {
      if (await this._hostPost(`/guests/${uuid}/admit`)) this.lobby = this.lobby.filter((g) => g.uuid !== uuid);
    },
    async refuseGuest(uuid) {
      if (await this._hostPost(`/guests/${uuid}/refuse`)) this.lobby = this.lobby.filter((g) => g.uuid !== uuid);
    },
    async removeGuest(participantKey) {
      if (!participantKey.startsWith('g:')) return;
      await this._hostPost(`/guests/${participantKey.slice(2)}/remove`);
    },
    async toggleLock() {
      const data = await this._hostPost('/lock', { locked: !this.meeting.locked });
      if (data) this.meeting.locked = !!data.locked;
    },
    async endMeeting() {
      const data = await this._hostPost('/end', undefined, {
        on409: 'There is no meeting in progress to end.',
      });
      if (data) this.leaveRoom?.();
    },
    isGuestTile(p) {
      return !!(p && typeof p.participant_key === 'string' && p.participant_key.startsWith('g:'));
    },
    capacityLabel() {
      const max = this.callSession && this.callSession.max_participants;
      const n = (this.callParticipants || []).length;
      return max ? `${n} / ${max}` : String(n);
    },
  };
};
