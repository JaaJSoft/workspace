window.pollDetail = function(pollId, csrfToken) {
  return {
    poll: null,
    loading: true,
    submitting: false,
    myVotes: {},
    finalizeSlotId: null,

    get isCreator() {
      const userId = document.getElementById('poll-app')?.dataset.userId;
      return userId && this.poll && String(this.poll.created_by?.id) === userId;
    },

    async init() {
      await this.loadPoll();
    },

    async loadPoll() {
      this.loading = true;
      try {
        const resp = await fetch('/api/v1/calendar/polls/' + pollId, {
          credentials: 'same-origin',
        });
        if (resp.ok) {
          this.poll = await resp.json();
          this._initMyVotes();
        }
      } catch (e) {
        console.error('Failed to load poll', e);
      }
      this.loading = false;
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    _initMyVotes() {
      const userId = document.getElementById('poll-app')?.dataset.userId;
      if (!userId) return;
      this.myVotes = {};
      for (const vote of (this.poll.votes || [])) {
        if (vote.user && String(vote.user.id) === userId) {
          this.myVotes[vote.slot_id] = vote.choice;
        }
      }
    },

    cycleVote(slotId) {
      if (this.poll.status === 'closed') return;
      const order = ['yes', 'maybe', 'no'];
      const current = this.myVotes[slotId];
      const idx = current ? order.indexOf(current) : -1;
      this.myVotes[slotId] = order[(idx + 1) % order.length];
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    voteClass(choice) {
      if (choice === 'yes') return 'bg-success/20 text-success';
      if (choice === 'maybe') return 'bg-warning/20 text-warning';
      if (choice === 'no') return 'bg-error/20 text-error';
      return 'bg-base-200';
    },

    voteIcon(choice) {
      if (choice === 'yes') return 'check';
      if (choice === 'maybe') return 'help-circle';
      if (choice === 'no') return 'x';
      return 'circle';
    },

    get participants() {
      if (!this.poll) return [];
      const userId = document.getElementById('poll-app')?.dataset.userId;
      const map = new Map();
      for (const vote of (this.poll.votes || [])) {
        // Skip current user's votes (shown in "My vote" row)
        if (vote.user && userId && String(vote.user.id) === userId) continue;
        const key = vote.user ? 'user:' + vote.user.id : 'guest:' + vote.guest_name;
        if (!map.has(key)) {
          map.set(key, {
            key,
            name: vote.user
              ? (vote.user.first_name + ' ' + vote.user.last_name).trim() || vote.user.username
              : vote.guest_name,
            votes: {},
          });
        }
        map.get(key).votes[vote.slot_id] = vote.choice;
      }
      return Array.from(map.values());
    },

    slotYesCount(slotId) {
      const slot = this.poll?.slots?.find(s => s.uuid === slotId);
      return slot?.yes_count || 0;
    },

    formatSlotDate(slot) {
      const d = new Date(slot.start);
      return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
    },

    formatSlotTime(slot) {
      const start = new Date(slot.start);
      const parts = [start.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })];
      if (slot.end) {
        const end = new Date(slot.end);
        parts.push(end.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }));
      }
      return parts.join(' - ');
    },

    async submitVotes() {
      this.submitting = true;
      try {
        const votes = Object.entries(this.myVotes).map(([slot_id, choice]) => ({
          slot_id,
          choice,
        }));
        const resp = await fetch('/api/v1/calendar/polls/' + pollId + '/vote', {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken,
          },
          body: JSON.stringify({ votes }),
        });
        if (resp.ok) {
          this.poll = await resp.json();
          this._initMyVotes();
        }
      } catch (e) {
        console.error('Failed to submit votes', e);
      }
      this.submitting = false;
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    async finalize() {
      if (!this.finalizeSlotId) return;
      this.submitting = true;
      try {
        const resp = await fetch('/api/v1/calendar/polls/' + pollId + '/finalize', {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken,
          },
          body: JSON.stringify({ slot_id: this.finalizeSlotId }),
        });
        if (resp.ok) {
          this.poll = await resp.json();
        }
      } catch (e) {
        console.error('Failed to finalize', e);
      }
      this.submitting = false;
      this.$nextTick(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    copyShareLink() {
      const url = window.location.origin + '/calendar/polls/shared/' + this.poll.share_token;
      navigator.clipboard.writeText(url);
    },
  };
};
