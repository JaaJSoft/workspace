// Polls UI: list, create, edit, detail (voting grid), finalize, share,
// and invitee management. Backend already lives in calendar/views_polls.py.
// Calls into pollUtils helpers from polls.js for vote-class/icon and
// slot date/time formatting (kept in polls.js because they're shared with
// the standalone /polls/<token> guest-vote page).
window.calendarPollsMixin = function calendarPollsMixin() {
  return {
    get filteredPolls() {
      if (!this.pollSearch.trim()) return this.polls;
      const q = this.pollSearch.toLowerCase();
      return this.polls.filter(p => p.title.toLowerCase().includes(q));
    },

    openPollList() {
      this.showPollListModal = true;
      this.pollSearch = '';
      this.loadPolls();

    },

    async loadPolls() {
      this.pollsLoading = true;
      try {
        const status = this.pollShowClosed ? 'all' : 'open';
        const resp = await fetch(`/api/v1/calendar/polls?filter=${this.pollFilter}&status=${status}`, { credentials: 'same-origin' });
        if (resp.ok) this.polls = await resp.json();
      } catch (e) {}
      this.pollsLoading = false;

    },

    setPollFilter(filter) {
      this.pollFilter = filter;
      this.loadPolls();
    },

    togglePollShowClosed() {
      this.pollShowClosed = !this.pollShowClosed;
      this.loadPolls();
    },

    openPollCreate() {
      this.showPollListModal = false;
      this.showPollCreateModal = true;
      this.pollForm = { title: '', description: '', slots: [{ start: '', end: '', showEnd: false }, { start: '', end: '', showEnd: false }] };
      this.pollFormError = null;

    },

    addPollSlot() {
      this.pollForm.slots.push({ start: '', end: '', showEnd: false });

    },

    removePollSlot(i) {
      if (this.pollForm.slots.length > 2) {
        this.pollForm.slots.splice(i, 1);

      }
    },

    async submitPoll() {
      if (!this.pollForm.title.trim()) return;
      const validSlots = this.pollForm.slots.filter(s => s.start);
      if (validSlots.length < 2) {
        this.pollFormError = 'At least 2 time slots with a start time are required.';
        return;
      }
      this.pollFormSubmitting = true;
      this.pollFormError = null;
      try {
        const resp = await fetch('/api/v1/calendar/polls', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify({
            title: this.pollForm.title.trim(),
            description: this.pollForm.description,
            slots: validSlots.map(s => ({
              start: new Date(s.start).toISOString(),
              end: s.end ? new Date(s.end).toISOString() : null,
            })),
          }),
        });
        if (resp.ok) {
          const created = await resp.json();
          this.showPollCreateModal = false;
          this.openPollDetail(created.uuid);
        } else {
          const data = await resp.json().catch(() => null);
          this.pollFormError = data?.detail || data?.slots?.[0] || 'Failed to create poll.';
        }
      } catch (e) {
        this.pollFormError = 'Network error. Please try again.';
      }
      this.pollFormSubmitting = false;
    },

    async openPollDetail(uuid) {
      this.showPollListModal = false;
      this.showPollDetailModal = true;
      this.currentPoll = null;
      this.currentPollLoading = true;
      this.pollMyVotes = {};
      this.pollFinalizeSlotId = null;
      this._setPollUrl(uuid);
      await this.loadPoll(uuid);

    },

    closePollDetail() {
      this.showPollDetailModal = false;
      this._setPollUrl(null);
    },

    _setPollUrl(pollId) {
      const params = new URLSearchParams(window.location.search);
      if (pollId) {
        params.set('poll', pollId);
      } else {
        params.delete('poll');
      }
      const qs = params.toString();
      const url = window.location.pathname + (qs ? '?' + qs : '');
      history.replaceState(null, '', url);
    },

    async loadPoll(uuid) {
      const requestId = ++this._loadPollRequestId;
      if (!isValidUuid(uuid)) {
        // Bail without leaving the modal stuck on its loading spinner if the
        // ?poll= query param was malformed.
        this.currentPollLoading = false;
        this.showPollDetailModal = false;
        this._setPollUrl(null);
        return;
      }
      this.currentPollLoading = true;
      try {
        const resp = await fetch(`/api/v1/calendar/polls/${uuid}`, { credentials: 'same-origin' });
        if (requestId !== this._loadPollRequestId) return;
        if (resp.ok) {
          const poll = await resp.json();
          if (requestId !== this._loadPollRequestId) return;
          // Pre-populate my votes
          const userId = String(document.body.dataset.userId);
          const myVotes = {};
          for (const vote of (poll.votes || [])) {
            if (vote.user && String(vote.user.id) === userId) {
              myVotes[vote.slot_id] = vote.choice;
            }
          }
          this.currentPoll = poll;
          this.pollMyVotes = myVotes;

        }
      } catch (e) {}
      if (requestId === this._loadPollRequestId) {
        this.currentPollLoading = false;
      }
    },

    pollCycleVote(slotId) {
      const cycle = ['yes', 'maybe', 'no'];
      const current = this.pollMyVotes[slotId];
      const idx = cycle.indexOf(current);
      const next = cycle[(idx + 1) % cycle.length];
      this.pollMyVotes = { ...this.pollMyVotes, [slotId]: next };

    },

    async submitPollVotes() {
      if (!this.currentPoll) return;
      const votes = Object.entries(this.pollMyVotes).map(([slot_id, choice]) => ({ slot_id, choice }));
      if (votes.length === 0) return;
      this.pollSubmitting = true;
      try {
        const resp = await fetch(`/api/v1/calendar/polls/${this.currentPoll.uuid}/vote`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify({ votes }),
        });
        if (resp.ok) {
          this.currentPoll = await resp.json();
          if (window.AppAlert) window.AppAlert.success('Votes saved!', { duration: 2000 });

        }
      } catch (e) {}
      this.pollSubmitting = false;
    },

    async finalizePoll() {
      if (!this.currentPoll || !this.pollFinalizeSlotId) return;
      this.pollSubmitting = true;
      try {
        const resp = await fetch(`/api/v1/calendar/polls/${this.currentPoll.uuid}/finalize`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify({ slot_id: this.pollFinalizeSlotId }),
        });
        if (resp.ok) {
          this.currentPoll = await resp.json();
          if (this.calendar) this.calendar.refetchEvents();
          this.refetchAgenda();
          if (window.AppAlert) window.AppAlert.success('Poll finalized! Event created.', { duration: 3000 });
        }
      } catch (e) {}
      this.pollSubmitting = false;
    },

    async deletePoll(uuid) {
      const ok = await AppDialog.confirm({
        title: 'Delete poll',
        message: 'Delete this poll and all its votes?',
        okLabel: 'Delete',
        okClass: 'btn-error',
        icon: 'trash-2',
        iconClass: 'bg-error/10 text-error',
      });
      if (!ok) return;
      try {
        const resp = await fetch(`/api/v1/calendar/polls/${uuid}`, {
          method: 'DELETE',
          credentials: 'same-origin',
          headers: { 'X-CSRFToken': getCSRFToken() },
        });
        if (resp.ok || resp.status === 204) {
          if (this.showPollDetailModal) {
            this.closePollDetail();
            this.openPollList();
          } else {
            this.loadPolls();
          }
        }
      } catch (e) {}
    },

    copyPollShareLink() {
      if (!this.currentPoll?.share_url) return;
      navigator.clipboard.writeText(this.currentPoll.share_url).then(() => {
        if (window.AppAlert) window.AppAlert.success('Share link copied!', { duration: 2000 });
      }).catch(() => {
        if (window.AppAlert) window.AppAlert.error('Failed to copy link');
      });
    },

    pollParticipants() {
      if (!this.currentPoll?.votes) return [];
      const currentUserId = String(document.body.dataset.userId);
      const map = new Map();
      for (const vote of this.currentPoll.votes) {
        if (vote.user && String(vote.user.id) === currentUserId) continue;
        const key = vote.user ? `u-${vote.user.id}` : `g-${vote.guest_name}`;
        if (!map.has(key)) {
          map.set(key, {
            key,
            name: vote.user ? vote.user.username : vote.guest_name,
            isUser: !!vote.user,
            userId: vote.user?.id,
            votes: {},
          });
        }
        map.get(key).votes[vote.slot_id] = vote.choice;
      }
      return Array.from(map.values());
    },

    pollVoteClass(choice) { return pollUtils.voteClass(choice); },
    pollVoteIcon(choice) { return pollUtils.voteIcon(choice); },
    pollChosenSlot() { return pollUtils.chosenSlot(this.currentPoll); },
    isPollChosenSlot(slotUuid) { return pollUtils.isChosenSlot(this.currentPoll, slotUuid); },
    formatPollSlotDate(slot) { return pollUtils.formatSlotDate(slot); },
    formatPollSlotTime(slot) { return pollUtils.formatSlotTime(slot); },

    async addPollInvitee(event) {
      const user = event.detail.user;
      if (!this.currentPoll) return;
      // Skip if already invited
      if ((this.currentPoll.invitees || []).find(i => i.user.id === user.id)) return;
      try {
        const resp = await fetch(`/api/v1/calendar/polls/${this.currentPoll.uuid}/invite`, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify({ user_ids: [user.id] }),
        });
        if (resp.ok) {
          this.currentPoll = await resp.json();
          if (window.AppAlert) window.AppAlert.success(`${user.username} invited!`, { duration: 2000 });

        }
      } catch (e) {}
    },

    async removePollInvitee(userId) {
      if (!this.currentPoll) return;
      try {
        const resp = await fetch(`/api/v1/calendar/polls/${this.currentPoll.uuid}/invite`, {
          method: 'DELETE',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify({ user_ids: [userId] }),
        });
        if (resp.ok) {
          this.currentPoll = await resp.json();
          if (window.AppAlert) window.AppAlert.success('Invitation removed', { duration: 2000 });

        }
      } catch (e) {}
    },

    isPollCreator() {
      if (!this.currentPoll) return false;
      return String(this.currentPoll.created_by?.id) === String(document.body.dataset.userId);
    },

    openPollEdit() {
      if (!this.currentPoll) return;
      this.showPollDetailModal = false;
      this.pollForm = {
        title: this.currentPoll.title,
        description: this.currentPoll.description || '',
        slots: (this.currentPoll.slots || []).map(s => ({
          uuid: s.uuid || null,
          start: this.toLocalDatetime(s.start),
          end: s.end ? this.toLocalDatetime(s.end) : '',
          showEnd: !!s.end,
        })),
      };
      // Ensure at least 2 slots
      while (this.pollForm.slots.length < 2) {
        this.pollForm.slots.push({ start: '', end: '', showEnd: false });
      }
      this.pollFormError = null;
      this.showPollEditModal = true;

    },

    async savePollEdit() {
      if (!this.currentPoll || !this.pollForm.title.trim()) return;
      const validSlots = this.pollForm.slots.filter(s => s.start);
      if (validSlots.length < 2) {
        this.pollFormError = 'At least 2 time slots with a start time are required.';
        return;
      }
      this.pollFormSubmitting = true;
      this.pollFormError = null;
      try {
        const resp = await fetch(`/api/v1/calendar/polls/${this.currentPoll.uuid}`, {
          method: 'PATCH',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify({
            title: this.pollForm.title.trim(),
            description: this.pollForm.description,
            slots: validSlots.map(s => ({
              ...(s.uuid ? { uuid: s.uuid } : {}),
              start: new Date(s.start).toISOString(),
              end: s.end ? new Date(s.end).toISOString() : null,
            })),
          }),
        });
        if (resp.ok) {
          this.currentPoll = await resp.json();
          this.showPollEditModal = false;
          this.showPollDetailModal = true;
          // Re-populate my votes (slots changed, old votes are gone)
          const userId = String(document.body.dataset.userId);
          const myVotes = {};
          for (const vote of (this.currentPoll.votes || [])) {
            if (vote.user && String(vote.user.id) === userId) {
              myVotes[vote.slot_id] = vote.choice;
            }
          }
          this.pollMyVotes = myVotes;
          if (window.AppAlert) window.AppAlert.success('Poll updated!', { duration: 2000 });

        } else {
          const data = await resp.json().catch(() => null);
          this.pollFormError = data?.detail || data?.slots?.[0] || 'Failed to update poll.';
        }
      } catch (e) {
        this.pollFormError = 'Network error. Please try again.';
      }
      this.pollFormSubmitting = false;
    },
  };
};
