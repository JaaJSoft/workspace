// Bot picker, retry/cancel, memories and scheduled-message management.
window.chatBotMixin = function chatBotMixin() {
  return {
    showBotPicker: false,
    availableBots: [],
    botFilter: '',
    botTyping: false,
    // Progress steps received for the active conversation's running
    // generation, in arrival order: [{ html }]. Each html is the
    // server-rendered tool summary row. Only rendered while botTyping is up.
    botSteps: [],
    _botStepTimer: null,

    botMemories: [],
    loadingBotMemories: false,
    memorySearch: '',

    scheduledMessages: [],
    loadingSchedules: false,

    agentGoals: [],
    loadingAgentGoals: false,

    // Draft for the optional "Agent mode" section of the bot picker.
    botGoalDraft: { enabled: false, goal: '', title: '', first_check_at: '', deadline: '' },

    // Full editor for an existing goal — the mission brief the agent reads at
    // every check-in, plus the schedule and its working notes.
    goalEditor: {
      open: false,
      saving: false,
      error: '',
      goal_uuid: null,
      title: '',
      goal: '',
      success_criteria: '',
      constraints: '',
      reporting: '',
      notes: '',
      deadline: '',
      next_check_at: '',
      check_count: 0,
      last_checked_at: null,
    },

    async fetchBots() {
      try {
        const resp = await fetch('/api/v1/ai/bots', { credentials: 'same-origin' });
        if (resp.ok) this.availableBots = await resp.json();
      } catch (e) {
        // AI may not be enabled — silently ignore
      }
    },

    async startBotConversation(bot) {
      this.showBotPicker = false;
      try {
        const resp = await fetch('/api/v1/chat/conversations', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
          },
          credentials: 'same-origin',
          body: JSON.stringify({
            member_ids: [bot.user_id],
          }),
        });
        if (!resp.ok) throw new Error('Failed to create conversation');
        const conv = await resp.json();
        if (!this.conversations.find(c => c.uuid === conv.uuid)) {
          this.conversations.unshift(conv);
          this.refreshConversationList();
        }
        await this._createDraftGoal(conv);
        await this.selectConversation(conv);
      } catch (e) {
        console.error('Failed to start bot conversation', e);
      }
    },

    // The datetime-local inputs produce naive local strings; convert to ISO
    // with offset so the backend doesn't have to guess the user's timezone.
    localInputToIso(value) {
      if (!value) return null;
      const d = new Date(value);
      return isNaN(d.getTime()) ? null : d.toISOString();
    },

    isoToLocalInput(iso) {
      if (!iso) return '';
      const d = new Date(iso);
      if (isNaN(d.getTime())) return '';
      const pad = n => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
        + `T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    },

    _goalDraftPayload() {
      const draft = this.botGoalDraft;
      const payload = { goal: draft.goal.trim() };
      if (draft.title.trim()) payload.title = draft.title.trim();
      const firstCheck = this.localInputToIso(draft.first_check_at);
      if (firstCheck) payload.first_check_at = firstCheck;
      const deadline = this.localInputToIso(draft.deadline);
      if (deadline) payload.deadline = deadline;
      return payload;
    },

    async _createDraftGoal(conv) {
      if (!this.botGoalDraft.enabled || !this.botGoalDraft.goal.trim()) {
        this.resetBotGoalDraft();
        return;
      }
      try {
        const resp = await fetch(`/api/v1/chat/conversations/${conv.uuid}/goals`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCSRFToken(),
          },
          credentials: 'same-origin',
          body: JSON.stringify(this._goalDraftPayload()),
        });
        if (!resp.ok) throw new Error(`Goal creation failed (${resp.status})`);
      } catch (e) {
        console.error('Failed to create agent goal', e);
      }
      this.resetBotGoalDraft();
    },

    resetBotGoalDraft() {
      this.botGoalDraft = { enabled: false, goal: '', title: '', first_check_at: '', deadline: '' };
    },

    isBotConversation(conv) {
      if (!conv) return false;
      if (conv.is_bot_conversation) return true;
      if (!conv.members) return false;
      return conv.members.some(m => this.availableBots.some(b => b.user_id === m.user.id));
    },

    isBotMessage(msg) {
      return this.availableBots.some(b => b.user_id === msg.author?.id);
    },

    _getBotMember() {
      if (!this.activeConversation?.members) return null;
      return this.activeConversation.members.find(m =>
        this.availableBots.some(b => b.user_id === m.user.id)
      );
    },

    clearBotStep() {
      clearTimeout(this._botStepTimer);
      this._botStepTimer = null;
      this.botSteps = [];
    },

    botTypingName() {
      const m = this._getBotMember();
      return m ? this.memberDisplayName(m) : 'AI';
    },

    async retryBotResponse(errorMsgUuid) {
      if (!this.activeConversation) return;
      const convId = this.activeConversation.uuid;

      // Remove the error message from the DOM immediately, in every surface
      // that renders it: leaving a copy behind shows an error for a response
      // that is being retried.
      this._messageEls(errorMsgUuid).forEach((el) => {
        el.closest('.msg-group')?.remove();
      });

      this.botTyping = true;
      this.clearBotStep();
      try {
        const res = await fetch(`/api/v1/chat/conversations/${convId}/messages/${errorMsgUuid}/retry`, {
          method: 'POST',
          headers: { 'X-CSRFToken': getCSRFToken() },
        });
        if (!res.ok) throw new Error('Retry failed');
      } catch (e) {
        console.error('Bot retry failed', e);
        this.botTyping = false;
        await this._refreshCurrentMessages();
      }
    },

    async cancelBotResponse() {
      if (!this.activeConversation) return;
      const convId = this.activeConversation.uuid;
      this.botTyping = false;
      this.clearBotStep();
      try {
        await fetch(`/api/v1/chat/conversations/${convId}/bot-cancel`, {
          method: 'POST',
          headers: { 'X-CSRFToken': getCSRFToken() },
          credentials: 'same-origin',
        });
      } catch (e) {
        console.error('Bot cancel failed', e);
      }
    },

    botTypingAvatar() {
      const m = this._getBotMember();
      if (m) {
        return window.userAvatarHtml(m.user.id, m.user.username, 'w-8 h-8 text-xs', { presence: false });
      }
      return '<div class="w-8 h-8 rounded-full bg-secondary flex items-center justify-center"><i data-lucide="sparkles" class="w-4 h-4 text-secondary-content"></i></div>';
    },

    // ── Bot memories ──────────────────────────────────────────
    // A method, not a getter: chatApp() composes its mixins with object
    // spread, and `{...mixin()}` copies a getter as the value it returned at
    // spread time — freezing the list to the empty array it held before the
    // fetch. A method keeps being re-evaluated, so Alpine tracks
    // botMemories / memorySearch and re-renders when they change.
    filteredBotMemories() {
      if (!this.memorySearch) return this.botMemories;
      const q = this.memorySearch.toLowerCase();
      return this.botMemories.filter(m =>
        m.key.toLowerCase().includes(q) || m.content.toLowerCase().includes(q)
      );
    },

    async loadBotMemories() {
      const botMember = this._getBotMember();
      if (!botMember) return;
      this.loadingBotMemories = true;
      this.botMemories = [];
      try {
        const resp = await fetch(`/api/v1/ai/memories?bot_id=${botMember.user.id}`, {
          credentials: 'same-origin',
        });
        if (resp.ok) this.botMemories = await resp.json();
      } catch (e) {
        console.error('Failed to load bot memories', e);
      }
      this.loadingBotMemories = false;
    },

    async editMemory(mem) {
      const content = await AppDialog.prompt({
        title: 'Edit memory',
        message: mem.key,
        value: mem.content,
        placeholder: 'Memory content...',
        okLabel: 'Save',
        inputSize: 'textarea',
        icon: 'brain',
        iconClass: 'bg-secondary/10 text-secondary',
      });
      if (content === null || content.trim() === mem.content) return;
      const resp = await fetch(`/api/v1/ai/memories/${mem.id}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCSRFToken(),
        },
        credentials: 'same-origin',
        body: JSON.stringify({ content: content.trim() }),
      });
      if (resp.ok) {
        mem.content = content.trim();
      }
    },

    async deleteMemory(mem) {
      const ok = await AppDialog.confirm({
        title: 'Delete memory',
        message: `Delete memory "${mem.key}"?`,
        okLabel: 'Delete',
        okClass: 'btn-error',
      });
      if (!ok) return;
      const resp = await fetch(`/api/v1/ai/memories/${mem.id}`, {
        method: 'DELETE',
        headers: { 'X-CSRFToken': getCSRFToken() },
        credentials: 'same-origin',
      });
      if (resp.ok) {
        this.botMemories = this.botMemories.filter(m => m.id !== mem.id);
      }
    },

    // ── Agent goals ─────────────────────────────────────────
    async loadAgentGoals(conversationId) {
      if (!this.activeConversation || !this.isBotConversation(this.activeConversation)) return;
      this.loadingAgentGoals = true;
      try {
        const resp = await fetch(`/api/v1/chat/conversations/${conversationId}/goals`, {
          credentials: 'same-origin',
        });
        const goals = resp.ok ? await resp.json() : null;
        // A slow response must not clobber the goals of a conversation the
        // user switched to while this request was in flight.
        if (this.activeConversation?.uuid !== conversationId) return;
        if (goals) this.agentGoals = goals;
      } catch (e) {
        console.error('Failed to load agent goals', e);
      } finally {
        if (this.activeConversation?.uuid === conversationId) {
          this.loadingAgentGoals = false;
        }
      }
    },

    editGoal(goal) {
      this.goalEditor = {
        open: true,
        saving: false,
        error: '',
        goal_uuid: goal.uuid,
        title: goal.title || '',
        goal: goal.goal || '',
        success_criteria: goal.success_criteria || '',
        constraints: goal.constraints || '',
        reporting: goal.reporting || '',
        notes: goal.notes || '',
        deadline: this.isoToLocalInput(goal.deadline),
        next_check_at: this.isoToLocalInput(goal.next_check_at),
        check_count: goal.check_count || 0,
        last_checked_at: goal.last_checked_at || null,
      };
    },

    closeGoalEditor() {
      this.goalEditor.open = false;
    },

    goalEditorPayload() {
      const form = this.goalEditor;
      return {
        title: form.title.trim(),
        goal: form.goal.trim(),
        success_criteria: form.success_criteria.trim(),
        constraints: form.constraints.trim(),
        reporting: form.reporting.trim(),
        notes: form.notes.trim(),
        // Clearing a datetime input means "no deadline"; the API takes null.
        deadline: this.localInputToIso(form.deadline),
        next_check_at: this.localInputToIso(form.next_check_at),
      };
    },

    async saveGoalEdit() {
      const payload = this.goalEditorPayload();
      if (!payload.title || !payload.goal) {
        this.goalEditor.error = 'Title and objective are required.';
        return;
      }
      // next_check_at is non-nullable on the model: an emptied input keeps the
      // current schedule instead of sending null and getting a 400 back.
      if (payload.next_check_at === null) delete payload.next_check_at;

      this.goalEditor.saving = true;
      this.goalEditor.error = '';
      const updated = await this._patchGoal({ uuid: this.goalEditor.goal_uuid }, payload);
      this.goalEditor.saving = false;
      if (updated) {
        this.goalEditor.open = false;
      } else {
        this.goalEditor.error = 'Could not save the goal. Please try again.';
      }
    },

    async toggleGoalPause(goal) {
      await this._patchGoal(goal, {
        status: goal.status === 'paused' ? 'active' : 'paused',
      });
    },

    async _patchGoal(goal, payload) {
      try {
        const resp = await fetch(
          `/api/v1/chat/conversations/${this.activeConversation.uuid}/goals/${goal.uuid}`,
          {
            method: 'PATCH',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': getCSRFToken(),
            },
            credentials: 'same-origin',
            body: JSON.stringify(payload),
          },
        );
        if (!resp.ok) return null;
        const updated = await resp.json();
        const idx = this.agentGoals.findIndex(g => g.uuid === goal.uuid);
        if (idx !== -1) this.agentGoals[idx] = updated;
        return updated;
      } catch (e) {
        console.error('Failed to update agent goal', e);
        return null;
      }
    },

    async stopGoal(goal) {
      const ok = await AppDialog.confirm({
        title: 'Stop goal',
        message: `Stop the goal "${goal.title}"? The bot will no longer work on it.`,
        okLabel: 'Stop',
        okClass: 'btn-error',
      });
      if (!ok) return;
      try {
        const resp = await fetch(
          `/api/v1/chat/conversations/${this.activeConversation.uuid}/goals/${goal.uuid}`,
          {
            method: 'DELETE',
            headers: { 'X-CSRFToken': getCSRFToken() },
            credentials: 'same-origin',
          },
        );
        if (resp.ok) {
          this.agentGoals = this.agentGoals.filter(g => g.uuid !== goal.uuid);
        }
      } catch (e) {
        console.error('Failed to stop agent goal', e);
      }
    },

    // ── Scheduled messages ──────────────────────────────────
    async loadScheduledMessages(conversationId) {
      if (!this.activeConversation || !this.isBotConversation(this.activeConversation)) return;
      this.loadingSchedules = true;
      try {
        const resp = await fetch(`/api/v1/chat/conversations/${conversationId}/schedules`, {
          credentials: 'same-origin',
        });
        if (resp.ok) {
          this.scheduledMessages = await resp.json();
        }
      } catch (e) {
        console.error('Failed to load schedules', e);
      }
      this.loadingSchedules = false;
    },

    scheduleTimingLabel(sched) {
      if (sched.kind === 'once') {
        return 'One-time';
      }
      let label = `Every ${sched.recurrence_interval > 1 ? sched.recurrence_interval + ' ' : ''}${sched.recurrence_unit}`;
      if (sched.recurrence_time) {
        label += ` at ${sched.recurrence_time.slice(0, 5)}`;
      }
      return label;
    },

    async editSchedule(sched) {
      const prompt = await AppDialog.prompt({
        title: 'Edit scheduled message',
        message: 'Update the instruction for this schedule:',
        value: sched.prompt,
        placeholder: 'Instruction...',
        okLabel: 'Save',
        inputSize: 'textarea',
        icon: 'clock',
        iconClass: 'bg-info/10 text-info',
      });
      if (prompt === null || prompt.trim() === sched.prompt) return;
      try {
        const resp = await fetch(
          `/api/v1/chat/conversations/${this.activeConversation.uuid}/schedules/${sched.uuid}`,
          {
            method: 'PATCH',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': getCSRFToken(),
            },
            credentials: 'same-origin',
            body: JSON.stringify({ prompt: prompt.trim() }),
          },
        );
        if (resp.ok) {
          const updated = await resp.json();
          const idx = this.scheduledMessages.findIndex(s => s.uuid === sched.uuid);
          if (idx !== -1) this.scheduledMessages[idx] = updated;
        }
      } catch (e) {
        console.error('Failed to edit schedule', e);
      }
    },

    async deleteSchedule(sched) {
      const ok = await AppDialog.confirm({
        title: 'Delete scheduled message',
        message: 'Delete this scheduled message?',
        okLabel: 'Delete',
        okClass: 'btn-error',
      });
      if (!ok) return;
      try {
        const resp = await fetch(
          `/api/v1/chat/conversations/${this.activeConversation.uuid}/schedules/${sched.uuid}`,
          {
            method: 'DELETE',
            headers: { 'X-CSRFToken': getCSRFToken() },
            credentials: 'same-origin',
          },
        );
        if (resp.ok) {
          this.scheduledMessages = this.scheduledMessages.filter(s => s.uuid !== sched.uuid);
        }
      } catch (e) {
        console.error('Failed to delete schedule', e);
      }
    },
  };
};
