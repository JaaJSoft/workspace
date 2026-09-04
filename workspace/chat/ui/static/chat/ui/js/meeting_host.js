// Host controls for a meeting room: the lobby (waiting guests), lock, remove
// and end. Spread into chatRoomApp; inert when the room has no meeting.
window.chatMeetingHostMixin = function chatMeetingHostMixin() {
  return {
    meeting: null,
    lobby: [],
    lobbyOpen: false,
    hostBusy: false,

    _hostHeaders() {
      return { 'Content-Type': 'application/json', 'X-CSRFToken': this._csrf() };
    },
    async _hostPost(path, body) {
      const resp = await fetch(`/api/v1/chat/meetings/${this.meeting.uuid}${path}`, {
        method: 'POST', headers: this._hostHeaders(), body: body ? JSON.stringify(body) : undefined,
      });
      if (!resp.ok) { window.AppAlert?.error('That did not work. Please try again.'); return null; }
      return resp.json();
    },

    async loadLobby() {
      if (!this.meeting) return;
      const resp = await fetch(`/api/v1/chat/meetings/${this.meeting.uuid}/lobby`);
      this.lobby = resp.ok ? await resp.json() : [];
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
      if (await this._hostPost('/end')) this.leaveRoom?.();
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
